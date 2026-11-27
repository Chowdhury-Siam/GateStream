package main

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"html/template"
	"io"
	"mime"
	"net"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/gotd/td/tgerr"
	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

func isNotFound(err error) bool { return errors.Is(err, mongo.ErrNoDocuments) }
func jsonResponse(w http.ResponseWriter, value any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(value)
}

type byteRange struct {
	Start, End int64
	Partial    bool
}

func parseRange(header string, size int64) (byteRange, error) {
	r := byteRange{0, size - 1, false}
	if size <= 0 {
		return r, errors.New("empty source")
	}
	if header == "" {
		return r, nil
	}
	if !strings.HasPrefix(header, "bytes=") || strings.Contains(header, ",") {
		return r, errors.New("unsupported range")
	}
	first, last, ok := strings.Cut(strings.TrimPrefix(header, "bytes="), "-")
	if !ok {
		return r, errors.New("invalid range")
	}
	digits := func(v string) bool {
		if v == "" {
			return false
		}
		for _, c := range v {
			if c < '0' || c > '9' {
				return false
			}
		}
		return true
	}
	if first == "" {
		if !digits(last) {
			return r, errors.New("invalid suffix")
		}
		n, err := strconv.ParseInt(last, 10, 64)
		if err != nil || n <= 0 {
			return r, errors.New("invalid suffix")
		}
		r.Start = max(0, size-n)
	} else {
		if !digits(first) {
			return r, errors.New("invalid start")
		}
		n, err := strconv.ParseInt(first, 10, 64)
		if err != nil || n >= size {
			return r, errors.New("range outside file")
		}
		r.Start = n
		if last != "" {
			if !digits(last) {
				return r, errors.New("invalid end")
			}
			n, err = strconv.ParseInt(last, 10, 64)
			if err != nil || n < r.Start {
				return r, errors.New("invalid end")
			}
			r.End = min(n, size-1)
		}
	}
	r.Partial = true
	return r, nil
}

func (a *App) chunk(ctx context.Context, doc bson.M, offset int64, sources map[*Worker]Source) ([]byte, error) {
	select {
	case a.readSlots <- struct{}{}:
		defer func() { <-a.readSlots }()
	case <-ctx.Done():
		return nil, ctx.Err()
	}
	var last error
	workers := a.workers
	for attempt := 0; attempt < max(3, min(12, len(workers)*3)); attempt++ {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		var chosen *Worker
		for _, worker := range workers {
			if worker.until.Load() <= time.Now().Unix() && (chosen == nil || worker.active.Load() < chosen.active.Load()) {
				chosen = worker
			}
		}
		if chosen == nil {
			return nil, errors.New("all workers are cooling down")
		}
		chosen.active.Add(1)
		source, ok := sources[chosen]
		if !ok {
			lookup, cancelLookup := context.WithTimeout(ctx, 10*time.Second)
			source, last = chosen.source(lookup, num(doc, "channel_id"), int(num(doc, "message_id")))
			cancelLookup()
			if last == nil {
				sources[chosen] = source
			}
		}
		var data []byte
		if last == nil {
			if source.Size != num(doc, "file_size") {
				last = errors.New("Telegram media size changed")
			} else {
				data, last = chosen.chunk(ctx, source, offset)
			}
		}
		chosen.active.Add(-1)
		if last == nil && int64(len(data)) >= min(1<<20, num(doc, "file_size")-offset) {
			return data, nil
		}
		if last == nil {
			last = io.ErrUnexpectedEOF
		}
		if tgerr.Is(last, "FILE_REFERENCE_EXPIRED", "FILE_REFERENCE_INVALID") {
			delete(sources, chosen)
		} else {
			chosen.until.Store(max(chosen.until.Load(), time.Now().Add(5*time.Second).Unix()))
		}
		// Every retry resolves the message on the selected account; references never cross bots.
		last = nil
	}
	return nil, errors.New("Telegram media retries exhausted")
}

func (a *App) download(w http.ResponseWriter, r *http.Request) {
	code := r.PathValue("code")
	doc, err := a.files.File(r.Context(), code)
	if err != nil {
		http.Error(w, "File not found", 404)
		return
	}
	size := num(doc, "file_size")
	name := str(doc, "file_name")
	if size <= 0 || name == "" {
		source, err := a.workers[0].source(r.Context(), num(doc, "channel_id"), int(num(doc, "message_id")))
		if err != nil {
			http.Error(w, "Media unavailable", 503)
			return
		}
		size = source.Size
		name = source.Name
		doc["file_size"] = size
		doc["file_name"] = name
		doc["mime_type"] = source.MIME
	}
	etag := `"` + digest(code+":"+strconv.FormatInt(size, 10)+":"+name) + `"`
	header := r.Header.Get("Range")
	if match := r.Header.Get("If-Range"); match != "" && match != etag {
		header = ""
	}
	br, err := parseRange(header, size)
	if err != nil {
		w.Header().Set("Content-Range", fmt.Sprintf("bytes */%d", size))
		http.Error(w, "Range not satisfiable", 416)
		return
	}
	length := br.End - br.Start + 1
	inline := r.URL.Query().Get("inline") == "1"
	disposition := "attachment"
	if inline && (strings.HasPrefix(mediaMIME(name, str(doc, "mime_type")), "video/") || strings.HasPrefix(mediaMIME(name, str(doc, "mime_type")), "audio/")) {
		disposition = "inline"
	}
	headers := func() {
		w.Header().Set("Accept-Ranges", "bytes")
		w.Header().Set("ETag", etag)
		w.Header().Set("Content-Type", mediaMIME(name, str(doc, "mime_type")))
		w.Header().Set("Content-Disposition", mime.FormatMediaType(disposition, map[string]string{"filename": name}))
		w.Header().Set("Content-Length", strconv.FormatInt(length, 10))
		w.Header().Set("Cache-Control", "private, no-store")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		if br.Partial {
			w.Header().Set("Content-Range", fmt.Sprintf("bytes %d-%d/%d", br.Start, br.End, size))
			w.WriteHeader(206)
		} else {
			w.WriteHeader(200)
		}
	}
	if r.Method == "HEAD" {
		headers()
		return
	}
	if a.streamSlots != nil {
		select {
		case a.streamSlots <- struct{}{}:
			defer func() { <-a.streamSlots }()
		default:
			w.Header().Set("Retry-After", "5")
			http.Error(w, "Download connections are busy. Retry shortly.", 503)
			return
		}
	}
	ctx, id := a.activity.begin(r.Context(), length, code)
	a.activity.setStop(id, func() { _ = http.NewResponseController(w).SetWriteDeadline(time.Now()) })
	failed := false
	defer func() { a.activity.finish(id, failed) }()
	sources := map[*Worker]Source{}
	cachedChunk := func(offset int64) ([]byte, error) {
		key := "go-" + digest(code+name+strconv.FormatInt(size, 10)) + "-" + strconv.FormatInt(offset, 10) + ".chunk"
		if b := a.cache.get(key); b != nil {
			return b, nil
		}
		b, err := a.chunk(ctx, doc, offset, sources)
		if err == nil && (offset < 4<<20 || offset >= max(0, size-(2<<20))) {
			a.cache.put(key, b)
		}
		return b, err
	}
	firstOffset := (br.Start / (1 << 20)) * (1 << 20)
	first, err := cachedChunk(firstOffset)
	if err != nil {
		failed = true
		http.Error(w, "Telegram media unavailable. Retry shortly.", 503)
		return
	}
	client := r.Header.Get("X-GateStream-Client")
	if len(client) != 64 || !hexValid(client) {
		host, _, _ := net.SplitHostPort(r.RemoteAddr)
		client = digest(host + "|" + r.UserAgent())
	}
	type chunkResult struct {
		data []byte
		err  error
	}
	ahead := make(chan chunkResult, 1)
	go func() {
		defer close(ahead)
		for offset := firstOffset + (1 << 20); offset <= br.End; offset += 1 << 20 {
			data, err := cachedChunk(offset)
			select {
			case ahead <- chunkResult{data, err}:
			case <-ctx.Done():
				return
			}
			if err != nil {
				return
			}
		}
	}()
	headers()
	position := br.Start
	for offset := firstOffset; position <= br.End; offset += 1 << 20 {
		data := first
		if offset != firstOffset {
			select {
			case next, ok := <-ahead:
				if !ok {
					err = io.ErrUnexpectedEOF
				} else {
					data = next.data
					err = next.err
				}
			case <-ctx.Done():
				return
			}
		}
		if err != nil {
			failed = ctx.Err() == nil
			panic(http.ErrAbortHandler)
		}
		left := max(0, position-offset)
		right := min(int64(len(data)), br.End-offset+1)
		if left >= right {
			failed = true
			panic(http.ErrAbortHandler)
		}
		n, writeErr := w.Write(data[left:right])
		a.activity.progress(id, int64(n), code+":"+client, position, size, !inline)
		position += int64(n)
		if writeErr != nil {
			return
		}
		if ctx.Err() != nil {
			return
		}
	}
}
func hexValid(s string) bool { _, err := hex.DecodeString(s); return err == nil }

func (a *App) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-GateStream-Version", version)
		if !a.ready.Load() {
			http.Error(w, "Starting", 503)
			return
		}
		io.WriteString(w, "OK")
	})
	mux.HandleFunc("GET /ping", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-GateStream-Version", version)
		io.WriteString(w, "OK")
	})
	mux.HandleFunc("GET /api/activity", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		jsonResponse(w, a.activity.snapshot())
	})
	mux.HandleFunc("GET /dl/{code}", a.download)
	mux.HandleFunc("GET /api/file/{code}", func(w http.ResponseWriter, r *http.Request) {
		doc, err := a.files.File(r.Context(), r.PathValue("code"))
		if err != nil {
			http.NotFound(w, r)
			return
		}
		jsonResponse(w, a.fileJSON(doc))
	})
	mux.HandleFunc("GET /api/batch/{code}", a.legacyBatch)
	mux.HandleFunc("GET /stream/{code}", func(w http.ResponseWriter, r *http.Request) {
		doc, err := a.files.File(r.Context(), r.PathValue("code"))
		if err != nil {
			http.NotFound(w, r)
			return
		}
		a.page(w, "player", map[string]any{"Name": str(doc, "file_name"), "Code": r.PathValue("code"), "Base": a.config.BaseURL})
	})
	mux.HandleFunc("GET /{$}", func(w http.ResponseWriter, r *http.Request) {
		a.page(w, "home", map[string]any{"Username": a.config.Username, "Owner": a.viewer(r, false) == a.config.Owner, "CSRF": a.csrf(w, r)})
	})
	mux.HandleFunc("GET /index", a.library)
	mux.HandleFunc("GET /index/sign-in", a.signIn)
	mux.HandleFunc("POST /index/sign-in", a.signIn)
	mux.HandleFunc("POST /index/sign-out", func(w http.ResponseWriter, r *http.Request) {
		if !a.validForm(w, r) {
			return
		}
		deleted := 0
		for _, cookie := range r.Cookies() {
			if cookie.Name != "gatestream_library" || len(cookie.Value) != 43 {
				continue
			}
			if _, err := a.store.db.Collection("library_sessions").DeleteOne(r.Context(), bson.M{"_id": digest(cookie.Value), "kind": "session"}); err != nil {
				http.Error(w, "Sign-out unavailable. Retry shortly.", 503)
				return
			}
			deleted++
			if deleted == 2 {
				break
			}
		}
		for _, path := range []string{"/", "/index"} {
			http.SetCookie(w, &http.Cookie{Name: "gatestream_library", MaxAge: -1, Path: path, HttpOnly: true, Secure: strings.HasPrefix(a.config.BaseURL, "https://"), SameSite: http.SameSiteLaxMode})
		}
		http.Redirect(w, r, "/index", 303)
	})
	mux.HandleFunc("GET /index/worker-setup", a.workerSetup)
	mux.HandleFunc("POST /index/worker-setup", a.workerSetup)
	mux.HandleFunc("POST /api/transfers/{id}/stop", func(w http.ResponseWriter, r *http.Request) {
		if !a.validForm(w, r) {
			return
		}
		if a.viewer(r, false) != a.config.Owner {
			http.Error(w, "Owner access required", 403)
			return
		}
		id, _ := strconv.ParseInt(r.PathValue("id"), 10, 64)
		jsonResponse(w, map[string]any{"stopped": a.activity.cancel(id)})
	})
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		if strings.HasPrefix(r.URL.Path, "/index") {
			w.Header().Set("Cache-Control", "private, no-store")
			w.Header().Set("X-Robots-Tag", "noindex, nofollow")
			w.Header().Set("X-Frame-Options", "DENY")
		}
		mux.ServeHTTP(w, r)
	})
}

func (a *App) page(w http.ResponseWriter, name string, values map[string]any) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if err := pages.ExecuteTemplate(w, name, values); err != nil {
		return
	}
}
func (a *App) csrf(w http.ResponseWriter, r *http.Request) string {
	if c, err := r.Cookie("gatestream_index_csrf"); err == nil && len(c.Value) == 64 {
		return c.Value
	}
	token := randomToken(32)
	http.SetCookie(w, &http.Cookie{Name: "gatestream_index_csrf", Value: token, Path: "/", HttpOnly: true, Secure: strings.HasPrefix(a.config.BaseURL, "https://"), SameSite: http.SameSiteLaxMode, MaxAge: 28800})
	return token
}
func (a *App) validForm(w http.ResponseWriter, r *http.Request) bool {
	r.Body = http.MaxBytesReader(w, r.Body, 8192)
	if r.ParseForm() != nil {
		http.Error(w, "Invalid form", 400)
		return false
	}
	cookie, err := r.Cookie("gatestream_index_csrf")
	if err != nil || len(cookie.Value) != 64 || !constantEqual(cookie.Value, r.Form.Get("csrf")) {
		http.Error(w, "Form expired. Reload and retry.", 403)
		return false
	}
	return true
}
func (a *App) viewer(r *http.Request, setup bool) int64 {
	name, kind := "gatestream_library", "session"
	if setup {
		name = "gatestream_worker_setup"
		kind = "worker_setup"
	}
	cookie, err := r.Cookie(name)
	if err != nil {
		return 0
	}
	doc, err := a.store.session(r.Context(), cookie.Value, kind, false)
	if err != nil {
		return 0
	}
	uid := num(doc, "user_id")
	if setup {
		if uid == a.config.Owner {
			return uid
		}
		return 0
	}
	if a.store.allowed(r.Context(), a.config.Owner, uid, 0) {
		return uid
	}
	return 0
}
func (a *App) signIn(w http.ResponseWriter, r *http.Request) {
	setup := r.URL.Query().Get("setup") == "workers"
	if setup && !strings.HasPrefix(a.config.BaseURL, "https://") {
		http.Error(w, "Worker setup requires HTTPS", 403)
		return
	}
	if r.Method == "POST" {
		if !a.validForm(w, r) {
			return
		}
		kind := "login"
		if setup {
			kind = "worker_setup_login"
		}
		doc, err := a.store.session(r.Context(), r.Form.Get("token"), kind, true)
		uid := num(doc, "user_id")
		if err != nil || !a.store.allowed(r.Context(), a.config.Owner, uid, 0) || (setup && uid != a.config.Owner) {
			http.Error(w, "Sign-in link expired or access was revoked.", 403)
			return
		}
		kind = "session"
		age := 8 * time.Hour
		name := "gatestream_library"
		target := "/index"
		if setup {
			kind = "worker_setup"
			age = 10 * time.Minute
			name = "gatestream_worker_setup"
			target = "/index/worker-setup"
		}
		token, err := a.store.newSession(r.Context(), uid, kind, age)
		if err != nil {
			http.Error(w, "Sign-in unavailable", 503)
			return
		}
		a.setSessionCookie(w, name, token, int(age.Seconds()), setup)
		http.Redirect(w, r, target, 303)
		return
	}
	a.page(w, "signin", map[string]any{"CSRF": a.csrf(w, r), "Setup": setup})
}
func (a *App) library(w http.ResponseWriter, r *http.Request) {
	csrf := a.csrf(w, r)
	uid := a.viewer(r, false)
	if uid == 0 {
		a.page(w, "library", map[string]any{"Username": a.config.Username})
		return
	}
	page, _ := strconv.ParseInt(r.URL.Query().Get("page"), 10, 32)
	page = max(1, page)
	q := r.URL.Query().Get("q")
	if len(q) > 100 {
		q = q[:100]
	}
	sources := bson.A{bson.M{"catalog_section": "personal"}}
	if a.config.Channel != 0 {
		sources = append(sources, bson.M{"catalog_section": bson.M{"$in": bson.A{"link", nil}}, "channel_id": a.config.Channel})
	}
	filter := bson.M{"user_id": uid, "episodes": bson.M{"$exists": false}, "$or": sources}
	if q != "" {
		filter["file_name"] = bson.M{"$regex": regexpQuote(q), "$options": "i"}
	}
	col := a.store.db.Collection("direct_files")
	total, err := col.CountDocuments(r.Context(), filter)
	if err != nil {
		http.Error(w, "Library unavailable", 503)
		return
	}
	count := max(1, (total+29)/30)
	page = min(page, count)
	cursor, err := col.Find(r.Context(), filter, options.Find().SetSort(bson.D{{Key: "created_at", Value: -1}, {Key: "_id", Value: 1}}).SetSkip((page-1)*30).SetLimit(30))
	if err != nil {
		http.Error(w, "Library unavailable", 503)
		return
	}
	defer cursor.Close(r.Context())
	var items []bson.M
	if cursor.All(r.Context(), &items) != nil {
		http.Error(w, "Library unavailable", 503)
		return
	}
	a.page(w, "library", map[string]any{"Signed": true, "Items": items, "Total": total, "Page": page, "Pages": count, "Prev": max(1, page-1), "Next": min(count, page+1), "Query": q, "QueryURL": q, "CSRF": csrf})
}
func (a *App) legacyBatch(w http.ResponseWriter, r *http.Request) {
	for _, col := range []string{"anime", "webseries"} {
		doc, err := a.store.find(r.Context(), col, bson.M{"_id": r.PathValue("code")})
		if err != nil {
			continue
		}
		var episodes []map[string]any
		values, _ := doc["episodes"].(bson.A)
		for _, v := range values {
			ep, ok := v.(bson.M)
			if !ok {
				continue
			}
			item := a.fileJSON(ep)
			delete(item, "status")
			item["episode_num"] = ep["episode_num"]
			episodes = append(episodes, item)
		}
		jsonResponse(w, map[string]any{"status": "success", "batch_id": doc["_id"], "title": doc["title"], "category": doc["category"], "channel_id": doc["channel_id"], "total_episodes": len(episodes), "episodes": episodes})
		return
	}
	http.NotFound(w, r)
}
func (a *App) fileJSON(doc bson.M) map[string]any {
	code := str(doc, "code")
	if code == "" {
		code = str(doc, "_id")
	}
	return map[string]any{"status": "success", "code": code, "file_name": str(doc, "file_name"), "file_size": num(doc, "file_size"), "size_formatted": humanSize(num(doc, "file_size")), "duration": num(doc, "duration"), "duration_formatted": str(doc, "duration_formatted"), "mime_type": str(doc, "mime_type"), "category": str(doc, "category"), "stream_url": a.config.BaseURL + "/stream/" + code, "download_url": a.config.BaseURL + "/dl/" + code}
}

func humanSize(n int64) string {
	value := float64(n)
	for _, unit := range []string{"B", "KiB", "MiB", "GiB", "TiB"} {
		if value < 1024 || unit == "TiB" {
			return fmt.Sprintf("%.1f %s", value, unit)
		}
		value /= 1024
	}
	return "0 B"
}

var pages = template.Must(template.New("pages").Funcs(template.FuncMap{"size": func(value any) string { return humanSize(integerValue(value)) }}).Parse(pageTemplates))

func (a *App) setSessionCookie(w http.ResponseWriter, name, token string, age int, setup bool) {
	cookiePath := "/"
	sameSite := http.SameSiteLaxMode
	secure := strings.HasPrefix(a.config.BaseURL, "https://")
	if setup {
		cookiePath = "/index/worker-setup"
		sameSite = http.SameSiteStrictMode
	} else {
		// Retire the earlier /index cookie so it cannot shadow the newly signed-in account.
		http.SetCookie(w, &http.Cookie{Name: name, Path: "/index", MaxAge: -1, HttpOnly: true, Secure: secure, SameSite: sameSite})
	}
	http.SetCookie(w, &http.Cookie{Name: name, Value: token, Path: cookiePath, MaxAge: age, HttpOnly: true, Secure: secure, SameSite: sameSite})
}
