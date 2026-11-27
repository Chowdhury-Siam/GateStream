package main

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"io"
	"mime/multipart"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

func (a *App) sendDocument(ctx context.Context, m Message, path, caption string) error {
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	reader, writer := io.Pipe()
	form := multipart.NewWriter(writer)
	req, err := http.NewRequestWithContext(ctx, "POST", "https://api.telegram.org/bot"+a.config.Token+"/sendDocument", reader)
	if err != nil {
		return errors.New("could not create upload request")
	}
	req.Header.Set("Content-Type", form.FormDataContentType())
	done := make(chan error, 1)
	go func() {
		_ = form.WriteField("chat_id", fmt.Sprint(m.Chat.ID))
		_ = form.WriteField("caption", caption)
		part, err := form.CreateFormFile("document", filepath.Base(path))
		if err == nil {
			_, err = io.Copy(part, file)
		}
		if err == nil {
			err = form.Close()
		}
		_ = writer.CloseWithError(err)
		done <- err
	}()
	response, err := (&http.Client{Timeout: 5 * time.Minute}).Do(req)
	if err != nil {
		_ = reader.CloseWithError(err)
		<-done
		return errors.New("Telegram document upload failed")
	}
	defer response.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 1<<20))
	_ = reader.Close()
	writeErr := <-done
	if response.StatusCode != 200 {
		return errors.New("Telegram document upload rejected")
	}
	return writeErr
}
func (a *App) backup(ctx context.Context, m Message) {
	a.reply(ctx, m, "Creating a database backup…", nil)
	file, err := os.CreateTemp(a.config.SessionDir, "GateStream_Backup_*.json.gz")
	if err != nil {
		a.reply(ctx, m, "Could not create the backup. Check disk quota.", nil)
		return
	}
	defer os.Remove(file.Name())
	zipper := gzip.NewWriter(file)
	_, err = io.WriteString(zipper, `{"meta":{"version":"1.0","database_name":`)
	if err == nil {
		err = json.NewEncoder(zipper).Encode(a.config.Database)
	}
	if err == nil {
		_, err = io.WriteString(zipper, `},"collections":{`)
	}
	count := 0
	for i, name := range fileCollections {
		if err != nil {
			break
		}
		if i > 0 {
			_, err = io.WriteString(zipper, ",")
		}
		if err != nil {
			break
		}
		_, err = fmt.Fprintf(zipper, "%q:[", name)
		if err != nil {
			break
		}
		cursor, queryErr := a.store.db.Collection(name).Find(ctx, bson.M{})
		if queryErr != nil {
			err = queryErr
			break
		}
		first := true
		for cursor.Next(ctx) {
			var doc bson.M
			if err = cursor.Decode(&doc); err != nil {
				break
			}
			var data []byte
			data, err = bson.MarshalExtJSON(doc, false, false)
			if err != nil {
				break
			}
			if !first {
				_, err = io.WriteString(zipper, ",")
				if err != nil {
					break
				}
			}
			first = false
			_, err = zipper.Write(data)
			if err != nil {
				break
			}
			count++
		}
		cursorErr := cursor.Err()
		_ = cursor.Close(ctx)
		if err == nil {
			err = cursorErr
		}
		if err == nil {
			_, err = io.WriteString(zipper, "]")
		}
	}
	if err == nil {
		_, err = io.WriteString(zipper, "}}")
	}
	zipErr := zipper.Close()
	fileErr := file.Close()
	if err != nil || zipErr != nil || fileErr != nil {
		a.reply(ctx, m, "Backup failed. Check MongoDB availability and disk quota.", nil)
		return
	}
	if a.sendDocument(ctx, m, file.Name(), fmt.Sprintf("GateStream backup · %d records · Keep this file private.", count)) != nil {
		a.reply(ctx, m, "Could not send the backup. Check Telegram limits and network access.", nil)
	}
}
func (a *App) restore(ctx context.Context, m Message) {
	if m.Reply == nil || m.Reply.Document == nil {
		a.reply(ctx, m, "Reply to a .json or .json.gz GateStream backup with /restore. Existing IDs will be updated.", nil)
		return
	}
	a.reply(ctx, m, "Reading and validating the backup…", nil)
	w := a.workers[0]
	source, err := w.source(ctx, m.Chat.ID, m.Reply.ID)
	if err != nil || source.Size <= 0 {
		a.reply(ctx, m, "Could not read the backup. Check Telegram access.", nil)
		return
	}
	file, err := os.CreateTemp(a.config.SessionDir, ".restore-")
	if err != nil {
		a.reply(ctx, m, "Could not stage the backup. Check disk quota.", nil)
		return
	}
	defer os.Remove(file.Name())
	defer file.Close()
	for offset := int64(0); offset < source.Size; offset += 1 << 20 {
		data, readErr := w.chunk(ctx, source, offset)
		if readErr != nil {
			a.reply(ctx, m, "Backup download failed.", nil)
			return
		}
		length := min(int64(len(data)), source.Size-offset)
		if _, err = file.Write(data[:length]); err != nil {
			a.reply(ctx, m, "Backup staging failed. Check disk quota.", nil)
			return
		}
	}
	validate := func(apply bool) (int, error) {
		if _, err := file.Seek(0, 0); err != nil {
			return 0, err
		}
		buffer := bufio.NewReader(file)
		var input io.Reader = buffer
		magic, _ := buffer.Peek(2)
		if bytes.Equal(magic, []byte{31, 139}) {
			zipper, err := gzip.NewReader(buffer)
			if err != nil {
				return 0, err
			}
			defer zipper.Close()
			input = zipper
		}
		return a.restoreJSON(ctx, input, apply)
	}
	if _, err = validate(false); err != nil {
		a.reply(ctx, m, "Invalid or incomplete GateStream backup. No records were imported.", nil)
		return
	}
	count, err := validate(true)
	if err != nil {
		a.reply(ctx, m, fmt.Sprintf("Restore stopped after %d records. Check MongoDB, then retry the same backup to continue.", count), nil)
		return
	}
	a.cache.clear()
	a.reply(ctx, m, fmt.Sprintf("Database restore complete. Imported %d records.", count), nil)
}
func (a *App) restoreJSON(ctx context.Context, input io.Reader, apply bool) (int, error) {
	decoder := json.NewDecoder(input)
	token, err := decoder.Token()
	if err != nil || token != json.Delim('{') {
		return 0, errors.New("expected object")
	}
	count := 0
	found := false
	for decoder.More() {
		key, err := decoder.Token()
		if err != nil {
			return count, err
		}
		if key != "collections" {
			var skip json.RawMessage
			if err = decoder.Decode(&skip); err != nil {
				return count, err
			}
			continue
		}
		found = true
		token, err = decoder.Token()
		if err != nil || token != json.Delim('{') {
			return count, errors.New("invalid collections")
		}
		for decoder.More() {
			key, err = decoder.Token()
			if err != nil {
				return count, err
			}
			name, _ := key.(string)
			allowed := false
			for _, col := range fileCollections {
				if col == name {
					allowed = true
				}
			}
			if !allowed {
				var skip json.RawMessage
				if err = decoder.Decode(&skip); err != nil {
					return count, err
				}
				continue
			}
			token, err = decoder.Token()
			if err != nil || token != json.Delim('[') {
				return count, errors.New("invalid collection")
			}
			for decoder.More() {
				var raw json.RawMessage
				if err = decoder.Decode(&raw); err != nil {
					return count, err
				}
				if len(raw) > 16<<20 {
					return count, errors.New("document too large")
				}
				var doc bson.M
				if err = bson.UnmarshalExtJSON(raw, false, &doc); err != nil {
					return count, err
				}
				if doc["_id"] == nil {
					return count, errors.New("missing record ID")
				}
				for _, key := range []string{"created_at", "updated_at"} {
					if v, ok := doc[key].(string); ok {
						if parsed, e := time.Parse(time.RFC3339Nano, v); e == nil {
							doc[key] = parsed
						}
					}
				}
				if apply {
					_, err = a.store.db.Collection(name).ReplaceOne(ctx, bson.M{"_id": doc["_id"]}, doc, options.Replace().SetUpsert(true))
					if err != nil {
						return count, err
					}
				}
				count++
			}
			if _, err = decoder.Token(); err != nil {
				return count, err
			}
		}
		if _, err = decoder.Token(); err != nil {
			return count, err
		}
	}
	if _, err = decoder.Token(); err != nil {
		return count, err
	}
	var trailing any
	if err = decoder.Decode(&trailing); err != io.EOF {
		return count, errors.New("trailing backup data")
	}
	if !found {
		return count, errors.New("missing collections")
	}
	return count, nil
}
func (a *App) rename(ctx context.Context, m Message, args []string) {
	if len(args) < 2 {
		a.reply(ctx, m, "Use /set_title FILE_CODE NEW_TITLE.", nil)
		return
	}
	target := linkCode(args[0])
	title := strings.Join(args[1:], " ")
	for _, name := range fileCollections {
		filter := bson.M{"$or": bson.A{bson.M{"_id": target}, bson.M{"title": bson.M{"$regex": regexp.QuoteMeta(target), "$options": "i"}}}}
		var doc bson.M
		err := a.store.db.Collection(name).FindOneAndUpdate(ctx, filter, bson.M{"$set": bson.M{"title": title, "updated_at": time.Now()}}).Decode(&doc)
		if err == nil {
			a.reply(ctx, m, "Title updated to "+html.EscapeString(title)+".", nil)
			return
		}
		if !isNotFound(err) {
			break
		}
	}
	a.reply(ctx, m, "Could not find or update this file.", nil)
}
func (a *App) purge(ctx context.Context, m Message) {
	start := m.ID - 100
	if m.Reply != nil {
		start = m.Reply.ID
	}
	start = max(1, start)
	count := 0
	for id := start; id < m.ID; id++ {
		if a.workers[0].api.call(ctx, "deleteMessage", map[string]any{"chat_id": m.Chat.ID, "message_id": id}, nil) == nil {
			count++
		}
		if ctx.Err() != nil {
			return
		}
	}
	a.reply(ctx, m, fmt.Sprintf("Deleted %d messages within Telegram permissions.", count), nil)
}
func (a *App) syncMetadata(ctx context.Context, m Message) {
	a.reply(ctx, m, "Refreshing stored file durations…", nil)
	count := 0
	for _, name := range fileCollections {
		cursor, err := a.store.db.Collection(name).Find(ctx, bson.M{})
		if err != nil {
			a.reply(ctx, m, "Could not read saved files.", nil)
			return
		}
		for cursor.Next(ctx) {
			var doc bson.M
			if cursor.Decode(&doc) != nil {
				continue
			}
			if num(doc, "message_id") > 0 {
				source, err := a.workers[0].source(ctx, num(doc, "channel_id"), int(num(doc, "message_id")))
				if err == nil {
					_, _ = a.store.db.Collection(name).UpdateOne(ctx, bson.M{"_id": doc["_id"]}, bson.M{"$set": bson.M{"duration": source.Duration, "duration_formatted": fmt.Sprintf("%d:%02d", source.Duration/60, source.Duration%60)}})
					count++
				}
			}
			episodes, _ := doc["episodes"].(bson.A)
			for _, value := range episodes {
				ep, ok := value.(bson.M)
				if !ok {
					continue
				}
				source, err := a.workers[0].source(ctx, num(doc, "channel_id"), int(num(ep, "message_id")))
				if err == nil {
					_, _ = a.store.db.Collection(name).UpdateOne(ctx, bson.M{"_id": doc["_id"], "episodes.code": ep["code"]}, bson.M{"$set": bson.M{"episodes.$.duration": source.Duration, "episodes.$.duration_formatted": fmt.Sprintf("%d:%02d", source.Duration/60, source.Duration%60)}})
					count++
				}
			}
		}
		_ = cursor.Close(ctx)
	}
	a.reply(ctx, m, fmt.Sprintf("Updated durations for %d files.", count), nil)
}

func titleQuery(name string) (string, int) {
	text := strings.TrimSuffix(name, filepath.Ext(name))
	text = regexp.MustCompile(`\[[^\]]*\]|@\w+|https?://\S+`).ReplaceAllString(text, " ")
	text = strings.NewReplacer("_", " ", ".", " ").Replace(text)
	year := 0
	var y int
	if found := regexp.MustCompile(`\b(?:19|20)\d{2}\b`).FindString(text); found != "" {
		fmt.Sscan(found, &y)
		year = y
	}
	text = regexp.MustCompile(`(?i)\b(?:s\d{1,2}(?:e\d{1,3})?|e\d{1,3}|season\s*\d+|episode\s*\d+|\d{3,4}p|4k|blu-?ray|web-?dl|webrip|hdtv|[xh]26[45]|hevc|av1|dual\s*audio|multi\s*audio)\b`).Split(text, 2)[0]
	text = regexp.MustCompile(`\b(?:19|20)\d{2}\b`).ReplaceAllString(text, " ")
	text = regexp.MustCompile(`\s+-\s*\d{1,4}\b.*$`).ReplaceAllString(text, "")
	text = strings.NewReplacer("(", " ", ")", " ", "{", " ", "}", " ").Replace(text)
	return strings.Trim(strings.Join(strings.Fields(text), " "), " -–—|:"), year
}
func similarity(a, b string) float64 {
	a = strings.ToLower(strings.Join(strings.Fields(a), " "))
	b = strings.ToLower(strings.Join(strings.Fields(b), " "))
	ar, br := []rune(a), []rune(b)
	if len(ar) < 2 || len(br) == 0 {
		return 0
	}
	row := make([]int, len(br)+1)
	for j := range row {
		row[j] = j
	}
	for i, x := range ar {
		previous := row[0]
		row[0] = i + 1
		for j, y := range br {
			old := row[j+1]
			cost := 1
			if x == y {
				cost = 0
			}
			row[j+1] = min(row[j+1]+1, row[j]+1, previous+cost)
			previous = old
		}
	}
	return 1 - float64(row[len(br)])/float64(max(len(ar), len(br)))
}
func (a *App) classify(ctx context.Context, name, mime string) (string, bson.M) {
	title, year := titleQuery(name)
	unmatched := bson.M{"category": "direct_files", "title": title, "status": "unmatched", "source": ""}
	if !strings.HasPrefix(mime, "video/") || len(title) < 2 {
		return "direct_files", unmatched
	}
	type candidate struct {
		title, category, source string
		score                   float64
		id                      any
	}
	var candidates []candidate
	requestJSON := func(method, endpoint string, payload any, headers map[string]string, out any) bool {
		address, _ := url.Parse(endpoint)
		source := address.Hostname()
		a.metadataMu.Lock()
		if a.metadataNext == nil {
			a.metadataNext = map[string]time.Time{}
		}
		wait := time.Until(a.metadataNext[source])
		if wait > 10*time.Second {
			a.metadataMu.Unlock()
			return false
		}
		a.metadataNext[source] = time.Now().Add(max(0, wait) + 2100*time.Millisecond)
		a.metadataMu.Unlock()
		if wait > 0 {
			select {
			case <-time.After(wait):
			case <-ctx.Done():
				return false
			}
		}

		var body io.Reader
		if payload != nil {
			data, _ := json.Marshal(payload)
			body = bytes.NewReader(data)
		}
		reqCtx, cancel := context.WithTimeout(ctx, 12*time.Second)
		defer cancel()
		req, err := http.NewRequestWithContext(reqCtx, method, endpoint, body)
		if err != nil {
			return false
		}
		req.Header.Set("Content-Type", "application/json")
		for k, v := range headers {
			req.Header.Set(k, v)
		}
		resp, err := (&http.Client{}).Do(req)
		if err != nil {
			return false
		}
		defer resp.Body.Close()
		if resp.StatusCode == 429 {
			seconds := int64(60)
			if value, err := strconv.ParseInt(resp.Header.Get("Retry-After"), 10, 64); err == nil {
				seconds = min(900, max(30, value))
			}
			a.metadataMu.Lock()
			a.metadataNext[source] = time.Now().Add(time.Duration(seconds) * time.Second)
			a.metadataMu.Unlock()
		}
		if resp.StatusCode != 200 {
			return false
		}
		return json.NewDecoder(io.LimitReader(resp.Body, 262144)).Decode(out) == nil
	}
	score := func(t string, y int) float64 {
		s := similarity(title, t)
		if year > 0 && y > 0 && (year-y > 1 || y-year > 1) {
			s -= .3
		}
		return s
	}
	var anilist struct {
		Data struct {
			Page struct {
				Media []struct {
					ID        int
					Title     struct{ Romaji, English, Native string }
					Synonyms  []string
					StartDate struct{ Year int }
				}
			}
		}
	}
	query := `query ($search: String!) {Page(perPage:5){media(search:$search,type:ANIME){id title{romaji english native} synonyms startDate{year}}}}`
	if requestJSON("POST", "https://graphql.anilist.co", map[string]any{"query": query, "variables": map[string]string{"search": title}}, nil, &anilist) {
		for _, item := range anilist.Data.Page.Media {
			best := float64(0)
			for _, t := range append([]string{item.Title.Romaji, item.Title.English, item.Title.Native}, item.Synonyms...) {
				best = max(best, score(t, item.StartDate.Year))
			}
			candidates = append(candidates, candidate{item.Title.Romaji, "anime", "AniList", best, item.ID})
		}
	}
	if a.config.MAL != "" {
		var mal struct {
			Data []struct {
				Node struct {
					ID           int
					Title        string
					Alternatives struct {
						English  string `json:"en"`
						Japanese string `json:"ja"`
						Synonyms []string
					} `json:"alternative_titles"`
					Date string `json:"start_date"`
				}
			}
		}
		if requestJSON("GET", "https://api.myanimelist.net/v2/anime?q="+url.QueryEscape(title)+"&limit=5&fields=alternative_titles,start_date", nil, map[string]string{"X-MAL-CLIENT-ID": a.config.MAL}, &mal) {
			for _, row := range mal.Data {
				item := row.Node
				var y int
				fmt.Sscanf(item.Date, "%d", &y)
				best := score(item.Title, y)
				for _, t := range append([]string{item.Alternatives.English, item.Alternatives.Japanese}, item.Alternatives.Synonyms...) {
					best = max(best, score(t, y))
				}
				candidates = append(candidates, candidate{item.Title, "anime", "MyAnimeList", best, item.ID})
			}
		}
	}
	if a.config.OMDB != "" {
		var movie struct {
			Response, Title, Year, Type string
			IMDB                        string `json:"imdbID"`
		}
		params := url.Values{"apikey": {a.config.OMDB}, "t": {title}, "plot": {"short"}}
		if year > 0 {
			params.Set("y", fmt.Sprint(year))
		}
		if requestJSON("GET", "https://www.omdbapi.com/?"+params.Encode(), nil, nil, &movie) && movie.Response == "True" {
			var y int
			fmt.Sscanf(movie.Year, "%d", &y)
			category := "movies"
			if movie.Type != "movie" {
				category = "webseries"
			}
			candidates = append(candidates, candidate{movie.Title, category, "OMDb", score(movie.Title, y), movie.IMDB})
		}
	}
	var best candidate
	for _, v := range candidates {
		if v.score > best.score {
			best = v
		}
	}
	if best.score >= .9 {
		for _, v := range candidates {
			if v.category != best.category && v.score >= best.score-.05 {
				return "direct_files", unmatched
			}
		}
		return best.category, bson.M{"category": best.category, "title": best.title, "status": "matched", "source": best.source, "score": best.score}
	}
	return "direct_files", unmatched
}
