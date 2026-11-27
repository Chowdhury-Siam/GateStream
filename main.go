package main

import (
	"context"
	"crypto/subtle"
	"errors"
	"fmt"
	"go.mongodb.org/mongo-driver/v2/bson"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"runtime"
	"runtime/debug"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

type FileRepository interface {
	File(context.Context, string) (bson.M, error)
}

type App struct {
	metadataMu    sync.Mutex
	metadataNext  map[string]time.Time
	streamSlots   chan struct{}
	files         FileRepository
	config        Config
	store         *Store
	workers       []*Worker
	dead          []string
	activity      *Activity
	cache         *Cache
	readSlots     chan struct{}
	ready         atomic.Bool
	ctx           context.Context
	stop          context.CancelFunc
	wg            sync.WaitGroup
	chatLocks     [32]sync.Mutex
	setupMu       sync.Mutex
	pending       *Pending
	repairMu      sync.Mutex
	workerCheckMu sync.Mutex
	restart       atomic.Bool
}

func main() {
	if err := run(); err != nil {
		log.Print(err)
		os.Exit(1)
	}
}
func run() error {
	if len(os.Args) > 1 && os.Args[1] == "--version" {
		fmt.Println("GateStream", version)
		return nil
	}
	root, err := os.Getwd()
	if err != nil {
		return err
	}
	lock, acquired, err := projectLock()
	if err != nil {
		return err
	}
	if !acquired {
		return nil
	}
	if lock != nil {
		defer lock.Close()
	}
	config, err := loadConfig(root)
	if err != nil {
		return err
	}
	output := configureLog(config)
	defer output.Close()
	if err := os.MkdirAll(config.SessionDir, 0700); err != nil {
		return errors.New("cannot create SESSION_DIR")
	}
	if err := os.Chmod(config.SessionDir, 0700); err != nil {
		return errors.New("cannot protect SESSION_DIR")
	}
	// The Go runtime limit guides garbage collection and includes all Go workers.
	debug.SetMemoryLimit(config.MemoryMB * 1024 * 1024)
	if _, set := os.LookupEnv("GOMAXPROCS"); !set {
		runtime.GOMAXPROCS(config.GoProcs)
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	// Bind before authenticating any Telegram clients. Bind failure leaves no services running.
	listener, err := net.Listen("tcp", net.JoinHostPort(config.Bind, config.Port))
	if err != nil {
		return fmt.Errorf("HTTP bind failed on %s:%s", config.Bind, config.Port)
	}
	defer listener.Close()
	log.Printf("GateStream %s · %s/%s · %s", version, runtime.GOOS, runtime.GOARCH, runtime.Version())
	log.Printf("HTTP bind %s:%s · auxiliary workers configured %d · media cache %s", config.Bind, config.Port, len(config.Workers), humanSize(config.CacheBytes))
	dbCtx, dbCancel := context.WithTimeout(ctx, 12*time.Second)
	store, err := openStore(dbCtx, config)
	dbCancel()
	if err != nil {
		return err
	}
	defer func() {
		closeCtx, closeCancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer closeCancel()
		_ = store.client.Disconnect(closeCtx)
	}()
	log.Print("MongoDB connected")
	a := &App{config: config, store: store, files: store, activity: newActivity(), cache: &Cache{dir: config.CacheDir, limit: config.CacheBytes, free: config.FreeBytes}, readSlots: make(chan struct{}, config.Concurrency), streamSlots: make(chan struct{}, config.MaxConnections), ctx: ctx, stop: cancel}
	defer a.close()
	for index, token := range append([]string{config.Token}, config.Workers...) {
		worker, err := startWorker(ctx, config, index, token, root)
		if err != nil {
			if index == 0 {
				return errors.New("primary Telegram client startup failed")
			}
			a.dead = append(a.dead, fmt.Sprintf("Worker %d · authentication or connection failed", index))
			log.Printf("Worker %d could not connect", index)
			continue
		}
		a.workers = append(a.workers, worker)
	}
	a.config.Username = a.workers[0].api.Me.Username
	log.Printf("Telegram clients connected %d/%d", len(a.workers), len(config.Workers)+1)
	flushCtx, flushCancel := context.WithTimeout(ctx, 12*time.Second)
	_ = a.activity.flush(flushCtx, store)
	flushCancel()
	server := &http.Server{Handler: a.routes(), ReadHeaderTimeout: 15 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 32 << 10}
	serveDone := make(chan error, 1)
	go func() { serveDone <- server.Serve(listener) }()
	a.ready.Store(true)
	log.Printf("HTTP server ready on %s:%s", config.Bind, config.Port)
	a.registerCommands(ctx)
	a.wg.Add(2)
	go func() { defer a.wg.Done(); a.poll(ctx) }()
	go func() {
		defer a.wg.Done()
		ticker := time.NewTicker(5 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				flushCtx, flushCancel := context.WithTimeout(ctx, 4*time.Second)
				_ = a.activity.flush(flushCtx, store)
				flushCancel()
				a.expireSetup()
				if a.config.MemoryRestartMB > 0 {
					var memory runtime.MemStats
					runtime.ReadMemStats(&memory)
					if memory.Sys-memory.HeapReleased > uint64(a.config.MemoryRestartMB)*1024*1024 {
						a.restart.Store(true)
						a.stop()
					}
				}
			}
		}
	}()
	select {
	case <-ctx.Done():
	case err := <-serveDone:
		if err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Print("HTTP server stopped unexpectedly")
		}
		cancel()
	}
	a.ready.Store(false)
	shutdown, shutdownCancel := context.WithTimeout(context.Background(), time.Duration(config.Shutdown)*time.Second)
	defer shutdownCancel()
	if server.Shutdown(shutdown) != nil {
		_ = server.Close()
	}
	a.close()
	finalFlush, cancelFlush := context.WithTimeout(context.Background(), 5*time.Second)
	_ = a.activity.flush(finalFlush, store)
	cancelFlush()
	log.Print("GateStream stopped cleanly")
	if a.restart.Load() {
		exe, err := os.Executable()
		if err != nil {
			return err
		}
		_ = store.client.Disconnect(shutdown)
		_ = output.Close()
		args := []string{filepath.Base(exe)}
		if lock != nil {
			args = append(args, "--watchdog")
		}
		return syscall.Exec(exe, args, os.Environ())
	}
	return nil
}
func (a *App) close() {
	a.stop()
	a.setupMu.Lock()
	a.closePending()
	a.setupMu.Unlock()
	for _, w := range a.workers {
		w.stop()
	}
	a.wg.Wait()
	for _, w := range a.workers {
		select {
		case <-w.done:
		case <-time.After(3 * time.Second):
		}
	}
}
func constantEqual(a, b string) bool { return subtle.ConstantTimeCompare([]byte(a), []byte(b)) == 1 }
func regexpQuote(s string) string    { return regexp.QuoteMeta(s) }
