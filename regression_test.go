package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/gotd/td/bin"
	"github.com/gotd/td/session"
	"github.com/gotd/td/telegram"
	"github.com/gotd/td/tg"
	"github.com/gotd/td/tgerr"
	"go.mongodb.org/mongo-driver/v2/bson"
)

func baseEnvironment(t *testing.T) {
	t.Helper()
	t.Setenv("TELEGRAM_API_ID", "12345")
	t.Setenv("TELEGRAM_API_HASH", strings.Repeat("f", 32))
	t.Setenv("TELEGRAM_BOT_TOKEN", "12345:"+strings.Repeat("p", 30))
	t.Setenv("OWNER_ID", "123456")
}
func TestConfigurationCommentsQuotesAndBOM(t *testing.T) {
	baseEnvironment(t)
	dir := t.TempDir()
	body := "\ufeff# PORT=7860\r\n# MULTI_BOT_TOKENS=99999:commented_example_token\r\n\r\nPORT=40978\r\nBIND_ADDRESS=127.0.0.1\r\nMULTI_BOT_TOKENS=\"\"\r\nOMDB_API_KEY=\"a $value # = b\"\r\nMAL_CLIENT_ID='space # equal= $secret'\r\nMEDIA_CACHE_MAX_MB=64\r\n"
	if err := os.WriteFile(filepath.Join(dir, ".env"), []byte(body), 0600); err != nil {
		t.Fatal(err)
	}
	c, err := loadConfig(dir)
	if err != nil {
		t.Fatal(err)
	}
	if c.Port != "40978" || c.Bind != "127.0.0.1" || len(c.Workers) != 0 || c.CacheBytes != 64<<20 {
		t.Fatal("configured HTTP, worker or cache values were ignored")
	}
	if c.OMDB != "a $value # = b" || c.MAL != "space # equal= $secret" {
		t.Fatal("quoted special characters were changed")
	}
}
func TestEnvironmentPrecedenceAndExplicitEmpty(t *testing.T) {
	baseEnvironment(t)
	dir := t.TempDir()
	_ = os.WriteFile(filepath.Join(dir, ".env"), []byte("PORT=40978\nMULTI_BOT_TOKENS=98765:pppppppppppppppppppppppppppppp\nOMDB_API_KEY=from-file\n"), 0600)
	t.Setenv("PORT", "19000")
	t.Setenv("MULTI_BOT_TOKENS", "")
	t.Setenv("OMDB_API_KEY", "")
	c, err := loadConfig(dir)
	if err != nil {
		t.Fatal(err)
	}
	if c.Port != "19000" || len(c.Workers) > 0 || c.OMDB != "" {
		t.Fatal("system environment did not override .env")
	}
	t.Setenv("PORT", "")
	if _, err = loadConfig(dir); err == nil || !strings.Contains(err.Error(), "PORT") {
		t.Fatal("empty numeric override should produce a validation error")
	}
}
func TestDuplicateAndInvalidWorkerTokens(t *testing.T) {
	one := "123:" + strings.Repeat("x", 30)
	two := "456:" + strings.Repeat("y", 30)
	tokens := parseTokens(one+","+two+";"+two+" \ninvalid 999:your_placeholder_example", one)
	if len(tokens) != 1 || tokens[0] != two {
		t.Fatal(tokens)
	}
}
func TestInvalidConfigurationSanitized(t *testing.T) {
	baseEnvironment(t)
	dir := t.TempDir()
	_ = os.WriteFile(filepath.Join(dir, ".env"), []byte("PORT=secret-password\n"), 0600)
	_, err := loadConfig(dir)
	if err == nil || strings.Contains(err.Error(), "secret-password") {
		t.Fatal("validation leaked a configured value")
	}
	_ = os.WriteFile(filepath.Join(dir, ".env"), []byte("MAL_CLIENT_ID=\"unterminated secret\n"), 0600)
	_, err = loadConfig(dir)
	if err == nil || strings.Contains(err.Error(), "unterminated secret") {
		t.Fatal("parser error leaked a configured value")
	}
}
func TestConfigAliasesAndDuplicates(t *testing.T) {
	baseEnvironment(t)
	t.Setenv("API_ID", "555")
	dir := t.TempDir()
	_ = os.WriteFile(filepath.Join(dir, ".env"), []byte("PORT=1234\nPORT=4321\n"), 0600)
	c, err := loadConfig(dir)
	if err != nil {
		t.Fatal(err)
	}
	if c.Port != "4321" {
		t.Fatal(c.Port)
	}
}
func TestByteRanges(t *testing.T) {
	for _, test := range []struct {
		value      string
		start, end int64
		bad        bool
	}{{"", 0, 99, false}, {"bytes=0-9", 0, 9, false}, {"bytes=50-", 50, 99, false}, {"bytes=-10", 90, 99, false}, {"bytes=0-999", 0, 99, false}, {"bytes=100-", 0, 0, true}, {"bytes=10-9", 0, 0, true}, {"bytes=0-1,3-5", 0, 0, true}, {"bytes=-0", 0, 0, true}, {"bytes=+0-2", 0, 0, true}, {"bytes=99999999999999999999999-", 0, 0, true}} {
		t.Run(test.value, func(t *testing.T) {
			r, err := parseRange(test.value, 100)
			if test.bad {
				if err == nil {
					t.Fatal("accepted invalid range")
				}
				return
			}
			if err != nil || r.Start != test.start || r.End != test.end {
				t.Fatal(r, err)
			}
		})
	}
}
func TestRangeCoverageConcurrentAndPlaybackExcluded(t *testing.T) {
	a := newActivity()
	ctx, id := a.begin(context.Background(), 100, "file")
	defer func() { a.finish(id, false); <-ctx.Done() }()
	var wg sync.WaitGroup
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			start := int64(i%10) * 10
			a.progress(id, 10, "client", start, 100, true)
		}(i)
	}
	wg.Wait()
	a.progress(id, 100, "playback", 0, 100, false)
	if got := num(a.snapshot()["phases"].(bson.M)["media"].(bson.M), "single_downloads"); got != 1 {
		t.Fatalf("counted %d completions", got)
	}
}
func TestRangeCoverageBoundAndExpiry(t *testing.T) {
	a := newActivity()
	_, id := a.begin(context.Background(), 3000, "file")
	defer a.finish(id, false)
	for i := 0; i < 1200; i++ {
		a.progress(id, 1, fmt.Sprint(i), 0, 100, true)
	}
	if len(a.coverage) != 1024 {
		t.Fatal(len(a.coverage))
	}
	for _, v := range a.coverage {
		v.Seen = time.Now().Add(-25 * time.Hour)
	}
	a.snapshot()
	if len(a.coverage) != 0 {
		t.Fatal("expired coverage retained")
	}
}
func TestHydrogramSessionAndCredentialCompatibility(t *testing.T) {
	key := bytes.Repeat([]byte{42}, 256)
	data := &session.Data{DC: 2, AuthKey: key}
	token, err := encodeHydrogram(data, 12345, 123456)
	if err != nil {
		t.Fatal(err)
	}
	restored, err := decodeHydrogram(token)
	if err != nil || restored.DC != 2 || !bytes.Equal(restored.AuthKey, key) {
		t.Fatal("Hydrogram session conversion failed", err)
	}
	a := &App{config: Config{Token: "12345:" + strings.Repeat("p", 30), Owner: 123456}}
	encrypted, err := a.seal([]byte(`[12345,"api-hash","session-value"]`))
	if err != nil {
		t.Fatal(err)
	}
	box, _ := a.cipher()
	plaintext, err := box.Open(nil, encrypted[:12], encrypted[12:], nil)
	if err != nil || string(plaintext) != `[12345,"api-hash","session-value"]` {
		t.Fatal("credential encryption failed")
	}
	encrypted[len(encrypted)-1] ^= 1
	if _, err = box.Open(nil, encrypted[:12], encrypted[12:], nil); err == nil {
		t.Fatal("tampered credentials accepted")
	}
}
func TestDiskCacheLimitsAndCleanup(t *testing.T) {
	dir := t.TempDir()
	unrelated := filepath.Join(dir, "keep.txt")
	_ = os.WriteFile(unrelated, []byte("keep"), 0600)
	c := &Cache{dir: dir, limit: 10}
	c.put("go-first.chunk", []byte("123456"))
	c.put("go-next.chunk", []byte("abcdef"))
	if c.size > c.limit || c.get("go-first.chunk") != nil || string(c.get("go-next.chunk")) != "abcdef" {
		t.Fatal("cache limit failed")
	}
	if c.clear() != 1 {
		t.Fatal("cache cleanup failed")
	}
	if _, err := os.Stat(unrelated); err != nil {
		t.Fatal("cleanup removed unrelated files")
	}
	c.free = 1 << 62
	c.put("go-full.chunk", []byte("data"))
	if c.size != 0 {
		t.Fatal("ignored disk reserve")
	}
}
func TestAPKContentType(t *testing.T) {
	if mediaMIME("installer.apk", "video/mp4") != "application/vnd.android.package-archive" {
		t.Fatal("APK labeled as a video")
	}
}

type fakeRPC struct {
	mu                sync.Mutex
	data              []byte
	references, reads int
	failAt            int64
	failed            bool
	active, max       atomic.Int64
}

func (f *fakeRPC) Close() error { return nil }
func (f *fakeRPC) Invoke(ctx context.Context, input bin.Encoder, output bin.Decoder) error {
	f.mu.Lock()
	switch req := input.(type) {
	case *tg.ChannelsGetMessagesRequest:
		f.references++
		ref := byte(f.references)
		output.(*tg.MessagesMessagesBox).Messages = &tg.MessagesChannelMessages{Messages: []tg.MessageClass{&tg.Message{ID: 1, Media: &tg.MessageMediaDocument{Document: &tg.Document{ID: 99, AccessHash: 777, FileReference: []byte{ref}, DCID: 2, Size: int64(len(f.data)), MimeType: "video/mp4", Attributes: []tg.DocumentAttributeClass{&tg.DocumentAttributeFilename{FileName: "installer.apk"}}}}}}}
		f.mu.Unlock()
		return nil
	case *tg.UploadGetFileRequest:
		f.reads++
		if req.Offset == f.failAt && !f.failed {
			f.failed = true
			f.mu.Unlock()
			return tgerr.New(400, "FILE_REFERENCE_EXPIRED")
		}
		if req.Offset >= int64(len(f.data)) {
			f.mu.Unlock()
			return io.EOF
		}
		data := append([]byte{}, f.data[req.Offset:min(req.Offset+int64(req.Limit), int64(len(f.data)))]...)
		f.mu.Unlock()
		active := f.active.Add(1)
		defer f.active.Add(-1)
		for previous := f.max.Load(); active > previous; previous = f.max.Load() {
			if f.max.CompareAndSwap(previous, active) {
				break
			}
		}
		select {
		case <-time.After(time.Millisecond):
		case <-ctx.Done():
			return ctx.Err()
		}
		output.(*tg.UploadFileBox).File = &tg.UploadFile{Bytes: data}
		return nil
	}
	f.mu.Unlock()
	return errors.New("unexpected Telegram RPC")
}

type fakeFiles struct{ doc bson.M }

func (f fakeFiles) File(context.Context, string) (bson.M, error) { return f.doc, nil }
func streamFixture(t *testing.T) (*App, *fakeRPC, *httptest.Server) {
	t.Helper()
	data := make([]byte, (3<<20)+17)
	for i := range data {
		data[i] = byte(i % 251)
	}
	rpc := &fakeRPC{data: data, failAt: 1 << 20}
	worker := &Worker{rpc: tg.NewClient(rpc), peers: map[int64]Peer{-1000000000001: {ID: 1, Hash: 7}}, pools: map[int]telegram.CloseInvoker{2: rpc}}
	a := &App{config: Config{BaseURL: "https://files.example", Owner: 123456}, files: fakeFiles{bson.M{"_id": "file", "code": "file", "channel_id": int64(-1000000000001), "message_id": 1, "file_size": int64(len(data)), "file_name": "installer.apk", "mime_type": "video/mp4"}}, workers: []*Worker{worker}, activity: newActivity(), cache: &Cache{dir: t.TempDir()}, readSlots: make(chan struct{}, 2)}
	a.ready.Store(true)
	server := httptest.NewServer(a.routes())
	t.Cleanup(server.Close)
	return a, rpc, server
}
func TestDownloadRecoveryAndHeaders(t *testing.T) {
	a, rpc, server := streamFixture(t)
	req, _ := http.NewRequest("GET", server.URL+"/dl/file", nil)
	req.Header.Set("Range", "bytes=500000-2600000")
	resp, err := server.Client().Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	got, err := io.ReadAll(resp.Body)
	if err != nil || resp.StatusCode != 206 || !bytes.Equal(got, rpc.data[500000:2600001]) {
		t.Fatal("range recovery failed", resp.StatusCode, len(got), err)
	}
	if resp.Header.Get("Content-Type") != "application/vnd.android.package-archive" || !strings.Contains(resp.Header.Get("Content-Disposition"), "installer.apk") {
		t.Fatal("download metadata changed")
	}
	rpc.mu.Lock()
	refs := rpc.references
	rpc.mu.Unlock()
	if refs != 2 {
		t.Fatalf("expected expired reference refresh, got %d lookups", refs)
	}
	if num(a.activity.snapshot()["phases"].(bson.M)["media"].(bson.M), "bytes") != int64(len(got)) {
		t.Fatal("delivered byte count incorrect")
	}
}
func TestHeadDoesNotReadTelegramAndInvalidRange(t *testing.T) {
	_, rpc, server := streamFixture(t)
	req, _ := http.NewRequest("HEAD", server.URL+"/dl/file", nil)
	req.Header.Set("Range", "bytes=2-8")
	resp, err := server.Client().Do(req)
	if err != nil {
		t.Fatal(err)
	}
	_ = resp.Body.Close()
	if resp.StatusCode != 206 || resp.ContentLength != 7 || rpc.reads != 0 || rpc.references != 0 {
		t.Fatal("HEAD accessed Telegram")
	}
	req, _ = http.NewRequest("GET", server.URL+"/dl/file", nil)
	req.Header.Set("Range", "bytes=999999999999-")
	resp, err = server.Client().Do(req)
	if err != nil {
		t.Fatal(err)
	}
	_ = resp.Body.Close()
	if resp.StatusCode != 416 {
		t.Fatal(resp.StatusCode)
	}
}
func TestDownloadRangesCombineOnce(t *testing.T) {
	a, rpc, server := streamFixture(t)
	rpc.failAt = -1
	split := len(rpc.data) / 2
	for _, r := range []string{fmt.Sprintf("bytes=0-%d", split-1), fmt.Sprintf("bytes=%d-", split), "bytes=0-20"} {
		req, _ := http.NewRequest("GET", server.URL+"/dl/file", nil)
		req.Header.Set("Range", r)
		req.Header.Set("X-GateStream-Client", strings.Repeat("b", 64))
		resp, err := server.Client().Do(req)
		if err != nil {
			t.Fatal(err)
		}
		_, err = io.Copy(io.Discard, resp.Body)
		_ = resp.Body.Close()
		if err != nil {
			t.Fatal(err)
		}
	}
	if num(a.activity.snapshot()["phases"].(bson.M)["media"].(bson.M), "single_downloads") != 1 {
		t.Fatal("range retries counted as extra downloads")
	}
}
func TestPlaybackAndHeadExcludedFromCompletion(t *testing.T) {
	a, _, server := streamFixture(t)
	resp, err := server.Client().Get(server.URL + "/dl/file?inline=1")
	if err != nil {
		t.Fatal(err)
	}
	_, err = io.Copy(io.Discard, resp.Body)
	_ = resp.Body.Close()
	if err != nil {
		t.Fatal(err)
	}
	if num(a.activity.snapshot()["phases"].(bson.M)["media"].(bson.M), "single_downloads") != 0 {
		t.Fatal("playback counted as a download")
	}
}
func TestConcurrentReadBound(t *testing.T) {
	a, rpc, _ := streamFixture(t)
	rpc.failAt = -1
	var wg sync.WaitGroup
	for i := 0; i < 12; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			doc := a.files.(fakeFiles).doc
			_, err := a.chunk(context.Background(), doc, 0, map[*Worker]Source{})
			if err != nil {
				t.Error(err)
			}
		}()
	}
	wg.Wait()
	if rpc.max.Load() > 2 {
		t.Fatal("unbounded Telegram reads", rpc.max.Load())
	}
}
func TestRenderAndAPICompatibility(t *testing.T) {
	a, _, server := streamFixture(t)
	for _, name := range []string{"home", "library", "signin", "setup", "player"} {
		var out bytes.Buffer
		err := pages.ExecuteTemplate(&out, name, map[string]any{"Owner": false, "CSRF": "csrf", "Step": "code", "Name": "<script>unsafe</script>", "Code": "file", "Signed": true, "Items": []bson.M{{"file_name": "<file>", "file_size": int32(10), "code": "file"}}})
		if err != nil {
			t.Fatal(name, err)
		}
		if strings.Contains(out.String(), "<script>unsafe") {
			t.Fatal("HTML injection")
		}
	}
	resp, err := server.Client().Get(server.URL + "/api/file/file")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var result map[string]any
	if json.NewDecoder(resp.Body).Decode(&result) != nil {
		t.Fatal("invalid JSON")
	}
	if result["code"] != "file" || result["download_url"] != "https://files.example/dl/file" || result["file_name"] != "installer.apk" {
		t.Fatal(result)
	}
	if _, ok := result["user_id"]; ok {
		t.Fatal("private uploader ID exposed")
	}
	_ = a
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func TestWorkerAPIAuthenticationFailureSanitized(t *testing.T) {
	token := "987:" + strings.Repeat("q", 30)
	api := &BotAPI{Token: token, HTTP: &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 401, Body: io.NopCloser(strings.NewReader(`{"ok":false,"error_code":401,"description":"secret invalid token"}`))}, nil
	})}}
	err := api.call(context.Background(), "getMe", map[string]any{}, nil)
	if err == nil || strings.Contains(err.Error(), token) || strings.Contains(err.Error(), "secret") {
		t.Fatal("authentication error leaked details")
	}
}
func TestTelegramCommandRegistration(t *testing.T) {
	var scopes []string
	api := &BotAPI{HTTP: &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		var request struct {
			Commands []struct{ Command string }
			Scope    struct{ Type string }
		}
		if json.NewDecoder(r.Body).Decode(&request) != nil {
			t.Fatal("invalid command registration")
		}
		for _, cmd := range request.Commands {
			switch cmd.Command {
			case "index", "drive", "batch", "privacy", "search", "sh":
				t.Fatal("retired command registered", cmd.Command)
			}
		}
		scopes = append(scopes, request.Scope.Type)
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(`{"ok":true,"result":true}`))}, nil
	})}}
	a := &App{config: Config{Owner: 1}, workers: []*Worker{{api: api}}}
	a.registerCommands(context.Background())
	if strings.Join(scopes, ",") != "default,all_private_chats,chat" {
		t.Fatal(scopes)
	}
}
func TestGracefulHTTPShutdownAndBindFailure(t *testing.T) {
	_, _, server := streamFixture(t)
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	if duplicate, err := net.Listen("tcp", listener.Addr().String()); err == nil {
		duplicate.Close()
		t.Fatal("duplicate bind succeeded")
	}
	httpServer := &http.Server{Handler: server.Config.Handler}
	done := make(chan error, 1)
	go func() { done <- httpServer.Serve(listener) }()
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err := httpServer.Shutdown(ctx); err != nil {
		t.Fatal(err)
	}
	if err := <-done; !errors.Is(err, http.ErrServerClosed) {
		t.Fatal(err)
	}
}
func TestLegacyBackupValidationBeforeWrites(t *testing.T) {
	a := &App{}
	raw := `{"meta":{"version":"1.0"},"collections":{"movies":[{"_id":"old","created_at":"2026-01-01T00:00:00Z"}],"direct_files":[]}}`
	count, err := a.restoreJSON(context.Background(), strings.NewReader(raw), false)
	if err != nil || count != 1 {
		t.Fatal(count, err)
	}
	if _, err = a.restoreJSON(context.Background(), strings.NewReader(raw[:len(raw)-2]), false); err == nil {
		t.Fatal("truncated backup accepted")
	}
}

type fakeMediaPools struct {
	primary, remote int
	pool            telegram.CloseInvoker
}

func (f *fakeMediaPools) Config() tg.Config                         { return tg.Config{ThisDC: 2} }
func (f *fakeMediaPools) Pool(int64) (telegram.CloseInvoker, error) { f.primary++; return f.pool, nil }
func (f *fakeMediaPools) DC(context.Context, int, int64) (telegram.CloseInvoker, error) {
	f.remote++
	return f.pool, nil
}
func TestMediaPoolsKeepPrimaryAuthorizationInItsOwnDC(t *testing.T) {
	rpc := &fakeRPC{data: []byte("media"), failAt: -1}
	pools := &fakeMediaPools{pool: rpc}
	worker := &Worker{media: pools, pools: map[int]telegram.CloseInvoker{}}
	data, err := worker.chunk(context.Background(), Source{DC: 2}, 0)
	if err != nil || string(data) != "media" || pools.primary != 1 || pools.remote != 0 {
		t.Fatal("primary DC attempted an authorization export", err)
	}
	_, err = worker.chunk(context.Background(), Source{DC: 3}, 0)
	if err != nil || pools.remote != 1 {
		t.Fatal("foreign DC did not receive a separate media pool", err)
	}
}

func TestLegacyNestedBSONRecordsRemainMaps(t *testing.T) {
	if !storedBSONOptions.DefaultDocumentM {
		t.Fatal("MongoDB client is not configured for Python-compatible nested maps")
	}
	raw, err := bson.Marshal(bson.M{"phases": bson.M{"media": bson.M{"bytes": int64(42), "single_downloads": int64(3), "zip_downloads": int64(2)}}, "episodes": bson.A{bson.M{"code": "old-episode"}}})
	if err != nil {
		t.Fatal(err)
	}
	decoder := bson.NewDecoder(bson.NewDocumentReader(bytes.NewReader(raw)))
	decoder.DefaultDocumentM()
	var record bson.M
	if err = decoder.Decode(&record); err != nil {
		t.Fatal(err)
	}
	phases, ok := record["phases"].(bson.M)
	if !ok {
		t.Fatal("saved phase map changed representation")
	}
	media, ok := phases["media"].(bson.M)
	if !ok || num(media, "bytes") != 42 || num(media, "single_downloads") != 3 || num(media, "zip_downloads") != 2 {
		t.Fatal("historical counters changed")
	}
	episodes, ok := record["episodes"].(bson.A)
	if !ok {
		t.Fatal("saved episode array changed representation")
	}
	episode, ok := episodes[0].(bson.M)
	if !ok || str(episode, "code") != "old-episode" {
		t.Fatal("saved episode map changed representation")
	}
}
