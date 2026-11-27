package main

import (
	"context"
	"fmt"
	"log"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
)

func TestNormalStartupBindFailure(t *testing.T) {
	baseEnvironment(t)
	root := t.TempDir()
	t.Chdir(root)
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	_, port, _ := net.SplitHostPort(listener.Addr().String())
	t.Setenv("PORT", port)
	t.Setenv("BIND_ADDRESS", "127.0.0.1")
	output := log.Writer()
	defer log.SetOutput(output)
	err = run()
	if err == nil || !strings.Contains(err.Error(), "HTTP bind failed") {
		t.Fatalf("startup did not stop at bind failure: %v", err)
	}
	if _, err = os.Stat(filepath.Join(root, "sessions-go", "bot_main.json")); !os.IsNotExist(err) {
		t.Fatal("Telegram session initialized before HTTP bind")
	}
}
func TestDownloadConnectionLimit(t *testing.T) {
	a, rpc, server := streamFixture(t)
	a.streamSlots = make(chan struct{}, 1)
	a.streamSlots <- struct{}{}
	response, err := server.Client().Get(server.URL + "/dl/file")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != 503 || response.Header.Get("Retry-After") != "5" || rpc.references != 0 {
		t.Fatal("capacity check performed unnecessary Telegram reads")
	}
}
func TestStopCancelsActiveTransfer(t *testing.T) {
	activity := newActivity()
	ctx, id := activity.begin(context.Background(), 100, "file")
	stopped := false
	activity.setStop(id, func() { stopped = true })
	if !activity.cancel(id) || !stopped || ctx.Err() == nil {
		t.Fatal("transfer cancellation failed")
	}
	activity.finish(id, false)
	if num(activity.snapshot()["phases"].(bson.M)["media"].(bson.M), "active") != 0 {
		t.Fatal("active connection did not clear")
	}
}
func TestSavedFileAPIKeepsEpisodeShape(t *testing.T) {
	app := &App{config: Config{BaseURL: "https://files.example"}}
	response := app.fileJSON(bson.M{"code": "legacy-episode", "file_name": "episode.mkv", "file_size": int32(25), "duration": int32(60), "category": "anime", "user_id": int64(99)})
	if response["file_size"] != int64(25) || response["download_url"] != "https://files.example/dl/legacy-episode" || response["stream_url"] != "https://files.example/stream/legacy-episode" {
		t.Fatal(response)
	}
	if _, ok := response["user_id"]; ok {
		t.Fatal("private field exposed")
	}
}
func TestTitleMatchingAndAmbiguousNames(t *testing.T) {
	title, year := titleQuery("[Release] Show.Name.2024.S01E02.1080p.mkv")
	if title != "Show Name" || year != 2024 {
		t.Fatal(title, year)
	}
	if similarity("Show Name", "show name") != 1 || similarity("Naruto", "Completely Different") >= .9 {
		t.Fatal("title matching is too permissive")
	}
}
func TestCacheAccountsForLegacyChunks(t *testing.T) {
	dir := t.TempDir()
	_ = os.WriteFile(filepath.Join(dir, "legacy_0.chunk"), []byte("123456"), 0600)
	cache := &Cache{dir: dir, limit: 8}
	cache.put("go-new.chunk", []byte("abcdef"))
	if cache.size > 8 {
		t.Fatal("legacy data escaped cache budget")
	}
	if _, err := os.Stat(filepath.Join(dir, "legacy_0.chunk")); !os.IsNotExist(err) {
		t.Fatal("old cache was not evicted to make space")
	}
}
func TestWatchdogHelper(t *testing.T) {
	if os.Getenv("GATESTREAM_TEST_LOCK_HELPER") == "" {
		return
	}
	originalExecutable := os.Args[0]
	os.Args = []string{originalExecutable, "--watchdog"}
	lock, acquired, err := projectLock()
	if err != nil {
		fmt.Println("ERROR", err)
		os.Exit(2)
	}
	if !acquired {
		fmt.Println("BUSY")
		os.Exit(0)
	}
	if os.Getenv("GATESTREAM_TEST_LOCK_HELPER") == "restart" {
		fmt.Printf("BEFORE %d\n", os.Getpid())
		_ = os.Setenv("GATESTREAM_TEST_LOCK_HELPER", "after")
		if err := syscall.Exec(originalExecutable, []string{originalExecutable, "-test.run=^TestWatchdogHelper$"}, os.Environ()); err != nil {
			os.Exit(3)
		}
	}
	fmt.Printf("ACQUIRED %d\n", os.Getpid())
	_ = lock.Close()
	os.Exit(0)
}
func TestWatchdogDuplicatePreventionAndExecRestart(t *testing.T) {
	t.Chdir(t.TempDir())
	t.Setenv("GATESTREAM_LOCK_FD", "")
	originalArgs := os.Args
	os.Args = []string{originalArgs[0], "--watchdog"}
	defer func() { os.Args = originalArgs }()
	lock, acquired, err := projectLock()
	if err != nil || !acquired {
		t.Fatal(acquired, err)
	}
	child := exec.Command(originalArgs[0], "-test.run=^TestWatchdogHelper$")
	child.Env = append(os.Environ(), "GATESTREAM_TEST_LOCK_HELPER=check", "GATESTREAM_LOCK_FD=")
	output, err := child.CombinedOutput()
	if err != nil || !strings.Contains(string(output), "BUSY") {
		t.Fatal(string(output), err)
	}
	_ = lock.Close()
	child = exec.Command(originalArgs[0], "-test.run=^TestWatchdogHelper$")
	child.Env = append(os.Environ(), "GATESTREAM_TEST_LOCK_HELPER=restart", "GATESTREAM_LOCK_FD=")
	output, err = child.CombinedOutput()
	if err != nil {
		t.Fatal(string(output), err)
	}
	var before, after int
	if _, err = fmt.Sscanf(string(output), "BEFORE %d\nACQUIRED %d", &before, &after); err != nil || before != after {
		t.Fatal("restart created a different process", string(output), err)
	}
}
func TestHTMLCannotExecuteAnAPK(t *testing.T) {
	_, _, server := streamFixture(t)
	req, _ := http.NewRequest("HEAD", server.URL+"/dl/file?inline=1", nil)
	response, err := server.Client().Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if !strings.HasPrefix(response.Header.Get("Content-Disposition"), "attachment") {
		t.Fatal("non-media file exposed inline")
	}
}

func TestRepairPacingDoesNotTruncateFractionalSeconds(t *testing.T) {
	now := time.Unix(1700000000, 999999999)
	until := time.Unix(0, int64(repairDeadline(now, 60*time.Second)*float64(time.Second)))
	if until.Sub(now) < 60*time.Second {
		t.Fatal("worker repair spacing rounded down", until.Sub(now))
	}
}

func TestBrowserSignInRetiresLegacyCookiePath(t *testing.T) {
	app := &App{config: Config{BaseURL: "https://files.example"}}
	response := httptest.NewRecorder()
	app.setSessionCookie(response, "gatestream_library", "fresh", 28800, false)
	cookies := response.Result().Cookies()
	if len(cookies) != 2 || cookies[0].Path != "/index" || cookies[0].MaxAge != -1 || cookies[1].Path != "/" || cookies[1].Value != "fresh" || !cookies[1].HttpOnly || !cookies[1].Secure {
		t.Fatal("legacy cookie could shadow the new account")
	}
	response = httptest.NewRecorder()
	app.setSessionCookie(response, "gatestream_worker_setup", "private", 600, true)
	cookies = response.Result().Cookies()
	if len(cookies) != 1 || cookies[0].Path != "/index/worker-setup" || cookies[0].SameSite != http.SameSiteStrictMode {
		t.Fatal("owner setup cookie scope changed")
	}
}
