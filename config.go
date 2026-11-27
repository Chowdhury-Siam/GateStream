package main

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"github.com/joho/godotenv"
	"io"
	"log"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
)

const version = "3.0.1"

type Config struct {
	GoProcs                                            int
	MaxConnections                                     int
	MemoryRestartMB                                    int64
	APIID                                              int
	APIHash, Token, Username                           string
	Owner, Channel                                     int64
	Workers                                            []string
	MongoURI, Database, OmniURI                        string
	Bind, Port, BaseURL, SessionDir, CacheDir, LogFile string
	CacheBytes, FreeBytes                              int64
	Concurrency, Handlers, Shutdown, SecretLength      int
	MemoryMB                                           int64
	MAL, OMDB                                          string
	RepairID                                           int
	RepairHash, RepairSession                          string
}

func loadConfig(root string) (Config, error) {
	sources := []map[string]string{{}}
	for _, item := range os.Environ() {
		k, v, _ := strings.Cut(item, "=")
		sources[0][k] = v
	}
	for _, name := range []string{".env", "config.env", "start.sh"} {
		data, err := os.ReadFile(filepath.Join(root, name))
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			return Config{}, fmt.Errorf("cannot read %s", name)
		}
		raw := strings.TrimPrefix(string(data), "\ufeff")
		if name == "start.sh" {
			var lines []string
			assignment := regexp.MustCompile(`^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*=`)
			for _, line := range strings.Split(raw, "\n") {
				if assignment.MatchString(line) && !strings.ContainsAny(line, "`;") && !strings.Contains(line, "$(") {
					lines = append(lines, line)
				}
			}
			raw = strings.Join(lines, "\n")
		}
		marker := "GATESTREAM_LITERAL_DOLLAR_" + randomToken(8)
		values, err := godotenv.Unmarshal(strings.ReplaceAll(raw, "$", marker))
		if err != nil {
			return Config{}, fmt.Errorf("invalid assignment in %s", name)
		}
		for key, value := range values {
			values[key] = strings.ReplaceAll(value, marker, "$")
		}
		sources = append(sources, values)
	}
	get := func(def string, names ...string) string {
		for _, source := range sources {
			for _, name := range names {
				if v, ok := source[name]; ok {
					legacy := regexp.MustCompile(`^\$\{([A-Za-z_][A-Za-z0-9_]*):-([^{}]*)\}$`).FindStringSubmatch(v)
					if len(legacy) == 3 {
						if exported, found := sources[0][legacy[1]]; found {
							return exported
						}
						return legacy[2]
					}
					return v
				}
			}
		}
		return def
	}
	var problems []string
	number := func(def, low, high int64, names ...string) int64 {
		v, err := strconv.ParseInt(strings.TrimSpace(get(strconv.FormatInt(def, 10), names...)), 10, 64)
		if err != nil || v < low || v > high {
			problems = append(problems, names[0]+" has an invalid integer value")
			return def
		}
		return v
	}
	c := Config{}
	c.APIID = int(number(0, 1, 2147483647, "TELEGRAM_API_ID", "API_ID"))
	c.APIHash = get("", "TELEGRAM_API_HASH", "API_HASH")
	c.Token = strings.TrimSpace(get("", "TELEGRAM_BOT_TOKEN", "BOT_TOKEN"))
	c.Username = strings.TrimPrefix(get("", "TELEGRAM_BOT_USERNAME", "BOT_USERNAME"), "@")
	c.Owner = number(0, 1, 9223372036854775807, "OWNER_ID")
	channel := strings.TrimSpace(get("0", "TELEGRAM_CHANNEL_ID", "CHANNEL_ID", "BIN_CHANNEL_ID"))
	if channel != "" {
		var err error
		c.Channel, err = strconv.ParseInt(channel, 10, 64)
		if err != nil || c.Channel > 0 {
			problems = append(problems, "TELEGRAM_CHANNEL_ID must be a negative integer")
		}
	}
	c.Workers = parseTokens(get("", "MULTI_BOT_TOKENS", "BOT_TOKENS"), c.Token)
	c.MongoURI = get("mongodb://localhost:27017", "DATABASE_URL", "MONGODB_URI")
	c.Database = get("OmniArchiver", "DATABASE_NAME")
	c.OmniURI = get("", "OMNI_MONGO_URI", "ANIME_MONGO_URI")
	defPort := int64(8080)
	if get("", "SPACE_ID") != "" {
		defPort = 7860
	}
	c.Port = strconv.FormatInt(number(defPort, 1, 65535, "PORT"), 10)
	c.Bind = get("0.0.0.0", "BIND_ADDRESS", "IP")
	c.BaseURL = strings.TrimRight(get("", "BASE_URL"), "/")
	if c.BaseURL == "" {
		c.BaseURL = "http://127.0.0.1:" + c.Port
		if host := get("", "SPACE_HOST"); host != "" {
			c.BaseURL = "https://" + host
		}
	}
	c.CacheBytes = number(500, 0, 1048576, "MEDIA_CACHE_MAX_MB") * 1024 * 1024
	c.FreeBytes = number(64, 0, 1048576, "CACHE_FREE_MB") * 1024 * 1024
	c.Concurrency = int(number(4, 1, 32, "MAX_TRANSMISSIONS"))
	c.MaxConnections = int(number(int64(max(8, c.Concurrency*4)), 1, 256, "MAX_HTTP_CONNECTIONS"))
	c.Handlers = int(number(2, 1, 32, "HANDLER_WORKERS"))
	c.GoProcs = int(number(4, 1, 32, "GO_MAX_PROCS", "IO_WORKERS"))
	c.Shutdown = int(number(20, 1, 120, "SHUTDOWN_TIMEOUT"))
	c.SecretLength = int(number(24, 8, 64, "SECRET_CODE_LENGTH"))
	c.MemoryMB = number(420, 32, 1048576, "MEMORY_CLEAN_MB")
	c.MemoryRestartMB = number(498, 0, 1048576, "MEMORY_RESTART_MB")
	if c.MemoryRestartMB != 0 && c.MemoryRestartMB <= c.MemoryMB {
		problems = append(problems, "MEMORY_RESTART_MB must exceed MEMORY_CLEAN_MB or be 0")
	}
	path := func(key, def string) string {
		p := get(def, key)
		if p == "" {
			p = def
		}
		if strings.HasPrefix(p, "~/") {
			home, _ := os.UserHomeDir()
			p = filepath.Join(home, p[2:])
		}
		if !filepath.IsAbs(p) {
			p = filepath.Join(root, p)
		}
		return p
	}
	c.SessionDir = path("SESSION_DIR", "sessions-go")
	c.CacheDir = path("MEDIA_CACHE_DIR", "cache/media")
	c.LogFile = path("LOG_FILE", "event-log.txt")
	c.MAL = get("", "MAL_CLIENT_ID")
	c.OMDB = get("", "OMDB_API_KEY")
	c.RepairHash = get("", "WORKER_REPAIR_API_HASH")
	c.RepairSession = get("", "WORKER_REPAIR_SESSION")
	if v := get("", "WORKER_REPAIR_API_ID"); v != "" {
		id, err := strconv.Atoi(v)
		if err != nil || id < 0 {
			problems = append(problems, "WORKER_REPAIR_API_ID is invalid")
		}
		c.RepairID = id
	}
	for name, value := range map[string]string{"TELEGRAM_API_HASH": c.APIHash, "TELEGRAM_BOT_TOKEN": c.Token, "DATABASE_URL": c.MongoURI, "DATABASE_NAME": c.Database} {
		if strings.TrimSpace(value) == "" {
			problems = append(problems, name+" must not be empty")
		}
	}
	if !regexp.MustCompile(`^[A-Fa-f0-9]{32}$`).MatchString(c.APIHash) {
		problems = append(problems, "TELEGRAM_API_HASH must contain 32 hexadecimal characters")
	}
	if !tokenPattern.MatchString(c.Token) || placeholders.MatchString(c.Token) {
		problems = append(problems, "TELEGRAM_BOT_TOKEN has invalid format")
	}
	u, err := url.Parse(c.BaseURL)
	if err != nil || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Scheme != "https" && u.Scheme != "http") {
		problems = append(problems, "BASE_URL must be an HTTP(S) URL without credentials, query or fragment")
	}
	if c.Bind == "" || strings.ContainsAny(c.Bind, " \r\n\t") {
		problems = append(problems, "BIND_ADDRESS is invalid")
	}
	for _, value := range []string{c.MongoURI, c.OmniURI} {
		if value != "" && !strings.HasPrefix(value, "mongodb://") && !strings.HasPrefix(value, "mongodb+srv://") {
			problems = append(problems, "invalid MongoDB URI scheme")
		}
	}
	if len(problems) > 0 {
		return c, errors.New(strings.Join(problems, ". "))
	}
	return c, nil
}

var tokenPattern = regexp.MustCompile(`^[0-9]+:[A-Za-z0-9_-]{20,}$`)
var placeholders = regexp.MustCompile(`(?i)example|placeholder|your[_-]|replace[_-]|abcdefghi|token[0-9]`)

func parseTokens(raw, primary string) []string {
	var result []string
	seen := map[string]bool{primary: true}
	for _, v := range strings.FieldsFunc(raw, func(r rune) bool { return r == ',' || r == ';' || r == ' ' || r == '\n' || r == '\t' || r == '\r' }) {
		if !seen[v] && tokenPattern.MatchString(v) && !placeholders.MatchString(v) {
			result = append(result, v)
			seen[v] = true
		}
	}
	return result
}

func randomToken(n int) string {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		panic("OS random source unavailable")
	}
	return hex.EncodeToString(b)
}

type safeLog struct {
	target  io.Writer
	secrets []string
}

func (s safeLog) Write(p []byte) (int, error) {
	text := string(p)
	for _, secret := range s.secrets {
		if secret != "" {
			text = strings.ReplaceAll(text, secret, "[REDACTED]")
		}
	}
	text = regexp.MustCompile(`[0-9]+:[A-Za-z0-9_-]{20,}`).ReplaceAllString(text, "[BOT_TOKEN]")
	_, err := io.WriteString(s.target, text)
	return len(p), err
}

type rotatingLog struct {
	mu   sync.Mutex
	file *os.File
	path string
	size int64
}

func (r *rotatingLog) Write(data []byte) (int, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.file == nil {
		return os.Stderr.Write(data)
	}
	if r.size+int64(len(data)) > 2<<20 {
		_ = r.file.Close()
		_ = os.Rename(r.path+".1", r.path+".2")
		_ = os.Rename(r.path, r.path+".1")
		next, err := os.OpenFile(r.path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
		if err != nil {
			r.file = nil
			return os.Stderr.Write(data)
		}
		r.file = next
		r.size = 0
	}
	n, err := r.file.Write(data)
	r.size += int64(n)
	if err != nil {
		return os.Stderr.Write(data)
	}
	return n, nil
}
func (r *rotatingLog) Close() error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.file == nil {
		return nil
	}
	err := r.file.Close()
	r.file = nil
	return err
}
func configureLog(c Config) io.Closer {
	secrets := append([]string{c.Token, c.APIHash, c.MongoURI, c.OmniURI, c.RepairHash, c.RepairSession, c.MAL, c.OMDB}, c.Workers...)
	file, err := os.OpenFile(c.LogFile, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		log.SetOutput(safeLog{os.Stderr, secrets})
		return io.NopCloser(strings.NewReader(""))
	}
	stat, _ := file.Stat()
	size := int64(0)
	if stat != nil {
		size = stat.Size()
	}
	rotating := &rotatingLog{file: file, path: c.LogFile, size: size}
	log.SetOutput(safeLog{io.MultiWriter(os.Stderr, rotating), secrets})
	return rotating
}
