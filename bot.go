package main

import (
	"context"
	"errors"
	"fmt"
	"html"
	"log"
	"net/url"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/gotd/td/tg"
	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

type Callback struct {
	ID      string  `json:"id"`
	From    User    `json:"from"`
	Message Message `json:"message"`
	Data    string  `json:"data"`
}
type Update struct {
	ID       int       `json:"update_id"`
	Message  *Message  `json:"message"`
	Callback *Callback `json:"callback_query"`
}
type Button struct {
	Text string `json:"text"`
	URL  string `json:"url,omitempty"`
	Data string `json:"callback_data,omitempty"`
}

func (a *App) reply(ctx context.Context, m Message, text string, buttons [][]Button) {
	input := map[string]any{"chat_id": m.Chat.ID, "text": text, "parse_mode": "HTML", "link_preview_options": map[string]bool{"is_disabled": true}, "reply_parameters": map[string]any{"message_id": m.ID, "allow_sending_without_reply": true}}
	if m.Thread > 0 {
		input["message_thread_id"] = m.Thread
	}
	if len(buttons) > 0 {
		input["reply_markup"] = map[string]any{"inline_keyboard": buttons}
	}
	if err := a.workers[0].api.call(ctx, "sendMessage", input, nil); err != nil {
		log.Print("Could not send Telegram reply")
	}
}
func (a *App) poll(ctx context.Context) {
	var handlers sync.WaitGroup
	queues := make([]chan Update, max(1, a.config.Handlers))
	for i := range queues {
		queues[i] = make(chan Update, 8)
		handlers.Add(1)
		go func(queue <-chan Update) {
			defer handlers.Done()
			for update := range queue {
				if ctx.Err() != nil {
					return
				}
				a.handle(ctx, update)
			}
		}(queues[i])
	}
	defer func() {
		for _, queue := range queues {
			close(queue)
		}
		handlers.Wait()
	}()
	offset := 0
	for ctx.Err() == nil {
		var updates []Update
		err := a.workers[0].api.call(ctx, "getUpdates", map[string]any{"offset": offset, "timeout": 25, "limit": 20, "allowed_updates": []string{"message", "callback_query"}}, &updates)
		if err != nil {
			select {
			case <-time.After(3 * time.Second):
			case <-ctx.Done():
				return
			}
			continue
		}
		for _, u := range updates {
			if ctx.Err() != nil {
				return
			}
			// Ordered handling prevents album updates and retry commands racing their own copy.
			chat := int64(u.ID)
			if u.Message != nil {
				chat = u.Message.Chat.ID
			}
			if u.Callback != nil {
				chat = u.Callback.Message.Chat.ID
			}
			queue := queues[uint64(chat)%uint64(len(queues))]
			select {
			case queue <- u:
			case <-ctx.Done():
				return
			}
			offset = u.ID + 1
		}
	}
}
func (a *App) handle(ctx context.Context, u Update) {
	if u.Callback != nil {
		a.callback(ctx, *u.Callback)
		return
	}
	if u.Message == nil {
		return
	}
	m := *u.Message
	if m.From.ID <= 0 || m.From.Bot {
		return
	}
	command, args := "", []string{}
	fields := strings.Fields(m.Text)
	if len(fields) > 0 && strings.HasPrefix(fields[0], "/") {
		target := strings.SplitN(strings.TrimPrefix(fields[0], "/"), "@", 2)
		if len(target) > 1 && !strings.EqualFold(target[1], a.config.Username) {
			return
		}
		command = strings.ToLower(target[0])
		args = fields[1:]
	}
	owner := m.From.ID == a.config.Owner
	if owner && (command == "auth" || command == "unauth") {
		a.authorize(ctx, m, command, args)
		return
	}
	if owner && command == "filetolink" {
		if !m.group() || m.Thread <= 1 {
			a.reply(ctx, m, "Open the File To Link forum topic and send /filetolink as a new message.", nil)
			return
		}
		err := a.store.set(ctx, "bot_settings", "file_to_link", bson.M{"chat_id": m.Chat.ID, "topic_id": m.Thread})
		if err != nil {
			a.reply(ctx, m, "Could not save the topic. Try again.", nil)
		} else {
			a.reply(ctx, m, "File To Link is active here. Authorized members can send files for automatic links.", nil)
		}
		return
	}
	chat := int64(0)
	if m.group() {
		chat = m.Chat.ID
	}
	if !a.store.allowed(ctx, a.config.Owner, m.From.ID, chat) {
		if !m.group() && (command == "start" || command == "help") {
			a.reply(ctx, m, "Ask the owner to authorize your Telegram ID <code>"+strconv.FormatInt(m.From.ID, 10)+"</code>.", nil)
		}
		return
	}
	topicActive := true
	if m.group() {
		setting, err := a.store.find(ctx, "bot_settings", bson.M{"_id": "file_to_link"})
		topicActive = err == nil && num(setting, "chat_id") == m.Chat.ID && num(setting, "topic_id") == int64(m.Thread) && m.Thread > 1
	}
	if command == "start" || command == "help" {
		if !m.group() && command == "start" {
			a.notice(ctx, m.From)
		}
		if !m.group() && len(args) == 1 && args[0] == "library" {
			a.loginLink(ctx, m, false)
			return
		}
		text := "<b>GateStream</b>\n\nSend a file to get its download link.\nOpen Library to see your saved files."
		buttons := [][]Button{{{Text: "Library", Data: "menu:library"}}, {{Text: "My ID", Data: "menu:id"}}}
		if m.group() {
			buttons = buttons[1:]
			text = "<b>GateStream</b>\n\nPost files in the File To Link topic for automatic download links."
			if owner && !topicActive {
				text += "\nSend /filetolink in that topic to connect it."
			}
		}
		if owner {
			buttons[len(buttons)-1] = append(buttons[len(buttons)-1], Button{Text: "Manage access", Data: "menu:access"})
			if !m.group() {
				buttons = append(buttons, []Button{{Text: "Set up worker repair", Data: "menu:workersetup"}})
			}
		}
		a.reply(ctx, m, text, buttons)
		return
	}
	if owner && m.group() && (command == "purge" || command == "clear") {
		a.purge(ctx, m)
		return
	}
	if command == "catalog" && !m.group() {
		a.loginLink(ctx, m, false)
		return
	}
	if m.media() != nil && (!m.group() || topicActive) {
		a.saveUpload(ctx, m, m)
		return
	}
	if command == "link" && m.group() {
		if !topicActive {
			return
		}
		if m.Reply != nil && m.Reply.From.ID == m.From.ID && m.Reply.Thread == m.Thread && m.Reply.media() != nil {
			a.saveUpload(ctx, *m.Reply, m)
		} else {
			a.reply(ctx, m, "Files posted here receive links automatically. To retry, reply to one of your files in this topic with /link.", nil)
		}
		return
	}
	if !owner || m.group() {
		return
	}
	switch command {
	case "link", "movie", "movies", "anime", "series", "link_anime", "link_movie", "link_series":
		a.channelLink(ctx, m, command, args)
	case "stats":
		a.stats(ctx, m)
	case "workers", "bots":
		a.checkWorkers(ctx, m, args)
	case "workersetup":
		a.loginLink(ctx, m, true)
	case "workerrepair":
		a.launchRepair(ctx, m, args)
	case "clean", "gc", "flush":
		count := a.cache.clear()
		runtime.GC()
		a.reply(ctx, m, fmt.Sprintf("Cleared %d cached media chunks.", count), nil)
	case "restart", "reboot":
		a.reply(ctx, m, "Restarting GateStream…", nil)
		a.restart.Store(true)
		a.stop()
	case "log":
		if a.sendDocument(ctx, m, a.config.LogFile, "GateStream log") != nil {
			a.reply(ctx, m, "Could not send the log file.", nil)
		}
	case "revoke", "del", "delete_link", "rm":
		if len(args) != 1 {
			a.reply(ctx, m, "Use /revoke FILE_CODE or a download link.", nil)
			return
		}
		code := linkCode(args[0])
		if a.store.revoke(ctx, code) != nil {
			a.reply(ctx, m, "Could not revoke the link.", nil)
		} else {
			a.cache.clear()
			a.reply(ctx, m, "Link revoked.", nil)
		}
	case "set_title", "rename", "name":
		a.rename(ctx, m, args)
	case "backup", "db_dump", "dump_db":
		a.backup(ctx, m)
	case "restore", "db_restore", "import_db":
		a.restore(ctx, m)
	case "purge", "clear":
		a.purge(ctx, m)
	case "sync_duration", "backfill", "sync_metadata":
		a.syncMetadata(ctx, m)
	}
}
func (a *App) authorize(ctx context.Context, m Message, command string, args []string) {
	var id int64
	if len(args) == 1 {
		id, _ = strconv.ParseInt(args[0], 10, 64)
	} else if len(args) == 0 {
		if m.Reply != nil {
			id = m.Reply.From.ID
		} else if m.group() {
			id = m.Chat.ID
		}
	}
	if id == 0 || id == a.config.Owner {
		a.reply(ctx, m, "Use /auth USER_ID or /auth GROUP_ID. In a group, /auth grants access to that group. /unauth removes access.\nSend /filetolink in the File To Link topic to connect it.", nil)
		return
	}
	var err error
	if command == "auth" {
		kind := "user"
		if id < 0 {
			kind = "group"
		}
		err = a.store.set(ctx, "bot_access", id, bson.M{"kind": kind})
	} else {
		_, err = a.store.db.Collection("bot_access").DeleteOne(ctx, bson.M{"_id": id})
	}
	if err != nil {
		a.reply(ctx, m, "Could not change authorization.", nil)
		return
	}
	action := "Authorized"
	if command == "unauth" {
		action = "Removed authorization for"
	}
	a.reply(ctx, m, fmt.Sprintf("%s <code>%d</code>.", action, id), nil)
}
func (a *App) loginLink(ctx context.Context, m Message, setup bool) {
	kind := "login"
	suffix := ""
	label := "Sign in to my library"
	if setup {
		if m.From.ID != a.config.Owner {
			return
		}
		if !strings.HasPrefix(a.config.BaseURL, "https://") {
			a.reply(ctx, m, "Worker setup requires an HTTPS BASE_URL.", nil)
			return
		}
		kind = "worker_setup_login"
		suffix = "?setup=workers"
		label = "Open worker setup"
	}
	token, err := a.store.newSession(ctx, m.From.ID, kind, 5*time.Minute)
	if err != nil {
		a.reply(ctx, m, "Could not create a sign-in link. Try again.", nil)
		return
	}
	a.reply(ctx, m, "Open this one-time private link. It expires in five minutes.", [][]Button{{{Text: label, URL: a.config.BaseURL + "/index/sign-in" + suffix + "#" + token}}})
}
func (a *App) saveUpload(ctx context.Context, m, response Message) {
	media := m.media()
	if media == nil {
		return
	}
	lower := strings.ToLower(media.Name)
	if strings.HasSuffix(lower, ".pickle") || (strings.HasSuffix(lower, ".json") && (strings.HasPrefix(lower, "credentials") || strings.HasPrefix(lower, "token"))) {
		a.reply(ctx, response, "Private credential files cannot be shared as media.", nil)
		return
	}
	if a.config.Channel == 0 {
		a.reply(ctx, response, "Uploads require TELEGRAM_CHANNEL_ID. Ask the owner to configure a storage channel.", nil)
		return
	}
	key := fmt.Sprintf("%d:%d", m.Chat.ID, m.ID)
	lock := &a.chatLocks[uint64(m.From.ID)%uint64(len(a.chatLocks))]
	lock.Lock()
	defer lock.Unlock()
	existing, err := a.store.find(ctx, "direct_files", bson.M{"source_key": key, "user_id": m.From.ID, "catalog_section": "personal"})
	if err == nil {
		if response.ID != m.ID {
			a.fileReply(ctx, response, existing)
		}
		return
	}
	if !isNotFound(err) {
		a.reply(ctx, response, "Could not access your library. Try again.", nil)
		return
	}
	var copied struct {
		ID int `json:"message_id"`
	}
	input := map[string]any{"chat_id": a.config.Channel, "from_chat_id": m.Chat.ID, "message_id": m.ID, "disable_notification": true}
	err = a.workers[0].api.call(ctx, "copyMessage", input, &copied)
	var limit *APIError
	if errors.As(err, &limit) && limit.Wait > 0 && limit.Wait <= 60 {
		select {
		case <-time.After(time.Duration(limit.Wait) * time.Second):
			err = a.workers[0].api.call(ctx, "copyMessage", input, &copied)
		case <-ctx.Done():
			return
		}
	}
	if err != nil {
		a.reply(ctx, response, "Could not copy this file to storage. Check the bot’s channel permissions.", nil)
		return
	}
	name := media.Name
	if name == "" {
		kind := "file"
		extension := ".bin"
		if m.Voice != nil {
			kind = "voice"
			extension = ".ogg"
		}
		if m.VideoNote != nil {
			kind = "video"
			extension = ".mp4"
		}
		name = fmt.Sprintf("%s_%d%s", kind, copied.ID, extension)
	}
	doc := bson.M{"channel_id": a.config.Channel, "message_id": copied.ID, "file_name": name, "file_size": media.Size, "mime_type": mediaMIME(name, media.MIME), "duration": media.Duration, "user_id": m.From.ID, "category": "direct_files", "title": name, "source_key": key}
	doc, err = a.store.saveFile(ctx, a.config, doc, true)
	if err != nil {
		a.reply(ctx, response, "Could not save the link. Send the file again.", nil)
		return
	}
	a.fileReply(ctx, response, doc)
}
func (a *App) fileReply(ctx context.Context, m Message, doc bson.M) {
	link := a.config.BaseURL + "/dl/" + str(doc, "code")
	buttons := []Button{{Text: "Download", URL: link}}
	contentType := str(doc, "mime_type")
	if strings.HasPrefix(contentType, "video/") || strings.HasPrefix(contentType, "audio/") {
		buttons = append(buttons, Button{Text: "Stream", URL: a.config.BaseURL + "/stream/" + str(doc, "code")})
	}
	details := humanSize(num(doc, "file_size"))
	if n := num(doc, "duration"); n > 0 {
		details += fmt.Sprintf(" · Duration %d:%02d", n/60, n%60)
	}
	revoke := "rm_" + str(doc, "code")
	if len(revoke) > 64 {
		token, err := a.store.newSession(ctx, m.From.ID, "revoke", 30*24*time.Hour)
		if err == nil {
			err = a.store.set(ctx, "library_sessions", digest(token), bson.M{"file_code": str(doc, "code")})
		}
		if err == nil {
			revoke = "rmt_" + token
		} else {
			a.reply(ctx, m, "Could not create the file controls. Retry /link on this file.", nil)
			return
		}
	}
	a.reply(ctx, m, "<b>File ready</b>\n"+html.EscapeString(str(doc, "file_name"))+"\n"+details+"\n\n<code>"+html.EscapeString(link)+"</code>", [][]Button{buttons, {{Text: "Revoke link", Data: revoke}}})
}
func (a *App) channelLink(ctx context.Context, m Message, command string, args []string) {
	category := ""
	if strings.Contains(command, "anime") {
		category = "anime"
	}
	if strings.Contains(command, "movie") {
		category = "movies"
	}
	if strings.Contains(command, "series") {
		category = "webseries"
	}
	var target string
	for _, arg := range args {
		switch strings.ToLower(arg) {
		case "anime", "animes":
			category = "anime"
		case "movie", "movies":
			category = "movies"
		case "series", "webseries", "tv":
			category = "webseries"
		default:
			if strings.Contains(arg, "t.me/") {
				target = arg
			}
		}
	}
	id, messageID, err := a.parsePost(ctx, target)
	if target == "" && m.Reply != nil && m.Reply.Forward != nil && m.Reply.Forward.Type == "channel" {
		id = m.Reply.Forward.Chat.ID
		messageID = m.Reply.Forward.ID
		err = nil
	}
	if err != nil {
		a.reply(ctx, m, "Use /link TELEGRAM_POST_LINK. Optionally add anime, movie, or series.", nil)
		return
	}
	source, err := a.workers[0].source(ctx, id, messageID)
	if err != nil {
		a.reply(ctx, m, "Could not read the channel post. Check the bot’s membership and permissions.", nil)
		return
	}
	metadata := bson.M{}
	if category == "" {
		category, metadata = a.classify(ctx, source.Name, source.MIME)
	}
	doc := bson.M{"channel_id": id, "message_id": messageID, "file_name": source.Name, "file_size": source.Size, "mime_type": source.MIME, "duration": source.Duration, "user_id": m.From.ID, "category": category, "title": source.Name, "metadata": metadata}
	doc, err = a.store.saveFile(ctx, a.config, doc, false)
	if err != nil {
		a.reply(ctx, m, "Could not save the file link.", nil)
		return
	}
	a.fileReply(ctx, m, doc)
}
func (a *App) parsePost(ctx context.Context, raw string) (int64, int, error) {
	raw = strings.TrimSpace(raw)
	if !strings.Contains(raw, "://") {
		raw = "https://" + raw
	}
	u, err := url.Parse(raw)
	host := ""
	if err == nil {
		host = strings.TrimPrefix(strings.ToLower(u.Host), "www.")
	}
	if err != nil || (host != "t.me" && host != "telegram.me") {
		return 0, 0, errors.New("invalid post URL")
	}
	parts := strings.Split(strings.Trim(u.Path, "/"), "/")
	if len(parts) < 2 {
		return 0, 0, errors.New("missing message")
	}
	msg, err := strconv.Atoi(parts[len(parts)-1])
	if err != nil || msg <= 0 {
		return 0, 0, errors.New("invalid message")
	}
	if parts[0] == "c" && len(parts) >= 3 {
		id, err := strconv.ParseInt(parts[1], 10, 64)
		if err != nil || id <= 0 {
			return 0, 0, errors.New("invalid channel")
		}
		return -1000000000000 - id, msg, nil
	}
	id, err := a.workers[0].resolveUsername(ctx, parts[0])
	return id, msg, err
}
func linkCode(raw string) string {
	if u, err := url.Parse(raw); err == nil && u.Host != "" {
		parts := strings.Split(strings.Trim(u.Path, "/"), "/")
		return parts[len(parts)-1]
	}
	return raw
}
func (a *App) callback(ctx context.Context, q Callback) {
	answer := func(text string) {
		_ = a.workers[0].api.call(ctx, "answerCallbackQuery", map[string]any{"callback_query_id": q.ID, "text": text, "show_alert": text != ""}, nil)
	}
	chat := int64(0)
	if q.Message.group() {
		chat = q.Message.Chat.ID
	}
	if !a.store.allowed(ctx, a.config.Owner, q.From.ID, chat) {
		answer("Access denied")
		return
	}
	q.Message.From = q.From
	switch q.Data {
	case "menu:id":
		answer(fmt.Sprintf("Your Telegram ID: %d\nChat ID: %d", q.From.ID, q.Message.Chat.ID))
	case "menu:library":
		answer("")
		if !q.Message.group() {
			a.loginLink(ctx, q.Message, false)
		}
	case "menu:workersetup":
		answer("")
		if !q.Message.group() && q.From.ID == a.config.Owner {
			a.loginLink(ctx, q.Message, true)
		}
	case "menu:access":
		answer("")
		if q.From.ID == a.config.Owner {
			a.reply(ctx, q.Message, "/auth USER_ID or GROUP_ID grants access.\n/unauth removes access.\n/filetolink connects the current group topic.", nil)
		}
	default:
		if !strings.HasPrefix(q.Data, "rm_") && !strings.HasPrefix(q.Data, "rmt_") {
			answer("Action unavailable")
			return
		}
		code := strings.TrimPrefix(q.Data, "rm_")
		if strings.HasPrefix(q.Data, "rmt_") {
			record, err := a.store.session(ctx, strings.TrimPrefix(q.Data, "rmt_"), "revoke", false)
			if err != nil {
				answer("This control expired. Use /revoke with the download link.")
				return
			}
			code = str(record, "file_code")
		}
		doc, err := a.files.File(ctx, code)
		if err != nil {
			answer("Link already revoked")
			return
		}
		if q.From.ID != a.config.Owner && q.From.ID != num(doc, "user_id") {
			answer("Only the uploader or owner can revoke this link")
			return
		}
		if a.store.revoke(ctx, code) != nil {
			answer("Could not revoke the link")
			return
		}
		a.cache.clear()
		answer("Link revoked")
		_ = a.workers[0].api.call(ctx, "editMessageText", map[string]any{"chat_id": q.Message.Chat.ID, "message_id": q.Message.ID, "text": "Link revoked"}, nil)
	}
}
func (a *App) stats(ctx context.Context, m Message) {
	snapshot := a.activity.snapshot()
	media := snapshot["phases"].(bson.M)["media"].(bson.M)
	var memory runtime.MemStats
	runtime.ReadMemStats(&memory)
	uploads, err := a.store.db.Collection("direct_files").CountDocuments(ctx, bson.M{})
	counts := fmt.Sprintf("Uploads %d", uploads)
	if err != nil {
		counts = "Library count unavailable"
	}
	text := fmt.Sprintf("<b>GateStream %s · Live stats</b>\n\nUptime %s\nBots connected %d/%d\nGo heap %s · Go runtime memory %s\n\n%s\n\nActive connections %d\nSent %s\nFiles completed %d\nTotals storage %s", version, time.Duration(snapshot["uptime_seconds"].(float64))*time.Second, len(a.workers), len(a.config.Workers)+1, humanSize(int64(memory.HeapAlloc)), humanSize(int64(memory.Sys)), counts, num(media, "active"), humanSize(num(media, "bytes")), num(media, "single_downloads"), snapshot["persistence"].(map[string]any)["state"])
	var usage syscall.Rusage
	if syscall.Getrusage(syscall.RUSAGE_SELF, &usage) == nil {
		seconds := float64(usage.Utime.Sec+usage.Stime.Sec) + float64(usage.Utime.Usec+usage.Stime.Usec)/1e6
		average := 100 * seconds / max(1, time.Since(a.activity.started).Seconds())
		text += fmt.Sprintf("\nPeak process RSS %s · Bot CPU average %.1f%%", humanSize(usage.Maxrss*1024), average)
	}
	var disk syscall.Statfs_t
	if syscall.Statfs(".", &disk) == nil {
		text += "\nDisk free " + humanSize(int64(disk.Bavail)*int64(disk.Bsize))
	}
	a.reply(ctx, m, text, nil)
}
func (a *App) checkWorkers(ctx context.Context, m Message, args []string) {
	if !a.workerCheckMu.TryLock() {
		a.reply(ctx, m, "A worker check is already running.", nil)
		return
	}
	defer a.workerCheckMu.Unlock()
	channel, err := a.repairChannelID(ctx, args)
	if err != nil {
		a.reply(ctx, m, err.Error(), nil)
		return
	}
	failures := append([]string{}, a.dead...)
	for i, w := range a.workers {
		check, cancel := context.WithTimeout(ctx, 10*time.Second)
		err := w.api.call(check, "sendMessage", map[string]any{"chat_id": channel, "text": fmt.Sprintf("GateStream worker check · @%s", w.api.Me.Username), "disable_notification": true}, nil)
		cancel()
		if err != nil {
			failures = append(failures, fmt.Sprintf("Worker %d @%s · Could not post", i, w.api.Me.Username))
		}
	}
	if len(failures) == 0 {
		a.reply(ctx, m, fmt.Sprintf("All %d bots posted to the log.", len(a.workers)), nil)
		return
	}
	a.reply(ctx, m, "<b>Workers requiring attention</b>\n"+html.EscapeString(strings.Join(failures, "\n")), nil)
}
func (a *App) notice(ctx context.Context, user User) {
	if a.config.Channel == 0 {
		return
	}
	col := a.store.db.Collection("bot_users")
	now := time.Now()
	until := now.Add(2 * time.Minute)
	_, err := col.UpdateOne(ctx, bson.M{"_id": user.ID}, bson.M{"$setOnInsert": bson.M{"first_seen": now}}, options.UpdateOne().SetUpsert(true))
	if err != nil {
		return
	}
	var doc bson.M
	err = col.FindOneAndUpdate(ctx, bson.M{"_id": user.ID, "notified_at": bson.M{"$exists": false}, "$or": bson.A{bson.M{"notify_until": bson.M{"$exists": false}}, bson.M{"notify_until": bson.M{"$lte": now}}}}, bson.M{"$set": bson.M{"notify_until": until}}).Decode(&doc)
	if err != nil {
		return
	}
	text := fmt.Sprintf("GateStream · New user\n\nUser ID: %d\nFirst name: %s\nLast name: %s\nUsername: %s\nLanguage: %s\nPremium: %t\nBot: %t", user.ID, user.First, user.Last, user.Username, user.Language, user.Premium, user.Bot)
	profile := fmt.Sprintf("tg://user?id=%d", user.ID)
	if user.Username != "" {
		profile = "https://t.me/" + user.Username
	}
	text += "\nProfile: " + profile
	lookup, cancelLookup := context.WithTimeout(ctx, 3*time.Second)
	users, lookupErr := a.workers[0].rawAPI().UsersGetUsers(lookup, []tg.InputUserClass{&tg.InputUser{UserID: user.ID}})
	cancelLookup()
	if lookupErr == nil {
		for _, value := range users {
			if detail, ok := value.(*tg.User); ok && detail.ID == user.ID {
				dc := "Not provided"
				if photo, ok := detail.Photo.(*tg.UserProfilePhoto); ok {
					dc = fmt.Sprint(photo.DCID)
				}
				text += fmt.Sprintf("\nVerified: %t\nRestricted: %t\nScam flag: %t\nFake flag: %t\nDeleted account: %t\nTelegram DC: %s", detail.Verified, detail.Restricted, detail.Scam, detail.Fake, detail.Deleted, dc)
			}
		}
	}
	firstSeen := time.Now()
	if stamp, ok := doc["first_seen"].(bson.DateTime); ok {
		firstSeen = stamp.Time()
	}
	text += "\nFirst start (UTC): " + firstSeen.UTC().Format("2006-01-02 15:04:05")
	err = a.workers[0].api.call(ctx, "sendMessage", map[string]any{"chat_id": a.config.Channel, "text": text, "disable_notification": true, "link_preview_options": map[string]bool{"is_disabled": true}}, nil)
	updates := bson.M{"$unset": bson.M{"notify_until": ""}}
	if err == nil {
		updates["$set"] = bson.M{"notified_at": time.Now()}
	}
	_, _ = col.UpdateOne(ctx, bson.M{"_id": user.ID, "notify_until": until}, updates)
}
func (a *App) registerCommands(ctx context.Context) {
	common := []map[string]string{{"command": "start", "description": "Open GateStream"}, {"command": "help", "description": "Show the menu"}}
	private := append(append([]map[string]string{}, common...), map[string]string{"command": "catalog", "description": "Open your personal library"})
	owner := append([]map[string]string{}, private...)
	for _, name := range []string{"auth", "unauth", "stats", "workers", "workersetup", "workerrepair", "link", "filetolink", "clean", "log", "backup", "restore", "restart", "revoke", "set_title", "sync_duration", "purge"} {
		owner = append(owner, map[string]string{"command": name, "description": strings.ReplaceAll(name, "_", " ")})
	}
	for _, scope := range []struct {
		kind     string
		commands []map[string]string
	}{{"default", common}, {"all_private_chats", private}, {"chat", owner}} {
		config := map[string]any{"type": scope.kind}
		if scope.kind == "chat" {
			config["chat_id"] = a.config.Owner
		}
		req, cancel := context.WithTimeout(ctx, 5*time.Second)
		_ = a.workers[0].api.call(req, "setMyCommands", map[string]any{"commands": scope.commands, "scope": config}, nil)
		cancel()
	}
}
