package main

import (
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha1"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/gotd/td/session"
	"github.com/gotd/td/telegram"
	"github.com/gotd/td/telegram/auth"
	"github.com/gotd/td/telegram/dcs"
	"github.com/gotd/td/tg"
	"github.com/gotd/td/tgerr"
	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

type Pending struct {
	browser, phone, hash, step string
	apiID                      int
	apiHash                    string
	client                     *telegram.Client
	storage                    *session.StorageMemory
	cancel                     context.CancelFunc
	done                       chan struct{}
	expires                    time.Time
	attempts                   int
}

func (a *App) closePending() {
	if a.pending != nil {
		a.pending.cancel()
		select {
		case <-a.pending.done:
		case <-time.After(2 * time.Second):
		}
		a.pending = nil
	}
}
func (a *App) expireSetup() {
	a.setupMu.Lock()
	defer a.setupMu.Unlock()
	if a.pending != nil && time.Now().After(a.pending.expires) {
		a.closePending()
	}
}
func connectedUser(ctx context.Context, id int, hash string, storage session.Storage) (*telegram.Client, context.CancelFunc, chan struct{}, error) {
	client := telegram.NewClient(id, hash, telegram.Options{NoUpdates: true, SessionStorage: storage})
	runCtx, stop := context.WithCancel(ctx)
	ready := make(chan struct{})
	done := make(chan struct{})
	go func() {
		defer close(done)
		_ = client.Run(runCtx, func(inner context.Context) error { close(ready); <-inner.Done(); return inner.Err() })
	}()
	select {
	case <-ready:
		return client, stop, done, nil
	case <-done:
		stop()
		return nil, nil, nil, errors.New("Telegram user connection failed")
	case <-time.After(20 * time.Second):
		stop()
		return nil, nil, nil, errors.New("Telegram user connection timed out")
	case <-ctx.Done():
		stop()
		return nil, nil, nil, ctx.Err()
	}
}

func (a *App) workerSetup(w http.ResponseWriter, r *http.Request) {
	if a.viewer(r, true) != a.config.Owner || !strings.HasPrefix(a.config.BaseURL, "https://") {
		http.Error(w, "Open /workersetup in the owner’s private chat", 403)
		return
	}
	csrf := a.csrf(w, r)
	cookie, _ := r.Cookie("gatestream_worker_setup")
	a.setupMu.Lock()
	defer a.setupMu.Unlock()
	if a.pending != nil && time.Now().After(a.pending.expires) {
		a.closePending()
	}
	step := "start"
	message := ""
	if a.pending != nil && a.pending.browser == cookie.Value {
		step = a.pending.step
	}
	if r.Method == "POST" {
		if !a.validForm(w, r) {
			return
		}
		action := r.Form.Get("action")
		switch {
		case a.pending != nil && a.pending.browser != cookie.Value:
			message = "Another setup is in progress. Finish it or wait five minutes."
		case action == "cancel":
			a.closePending()
			step = "start"
			message = "Setup canceled."
		case action != step:
			message = "This form expired. Continue with the current step."
		default:
			req, cancel := context.WithTimeout(r.Context(), 25*time.Second)
			if step == "start" {
				step, message = a.startSetup(req, cookie.Value, r)
			} else {
				step, message = a.verifySetup(req, r)
			}
			cancel()
		}
	}
	w.Header().Set("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
	if step == "done" {
		_, _ = a.store.db.Collection("library_sessions").DeleteOne(r.Context(), bson.M{"_id": digest(cookie.Value), "kind": "worker_setup"})
		http.SetCookie(w, &http.Cookie{Name: "gatestream_worker_setup", Path: "/index/worker-setup", MaxAge: -1, Secure: true, HttpOnly: true, SameSite: http.SameSiteStrictMode})
	}
	a.page(w, "setup", map[string]any{"Step": step, "Message": message, "CSRF": csrf})
}
func (a *App) startSetup(ctx context.Context, browser string, r *http.Request) (string, string) {
	id, err := strconv.Atoi(r.Form.Get("api_id"))
	hash := r.Form.Get("api_hash")
	phone := r.Form.Get("phone")
	if err != nil || id <= 0 || len(hash) != 32 || len(phone) < 7 || len(phone) > 24 {
		return "start", "Enter a valid API ID, API hash and phone number."
	}
	storage := &session.StorageMemory{}
	client, stop, done, err := connectedUser(a.ctx, id, hash, storage)
	if err != nil {
		return "start", "Could not connect to Telegram. Try again later."
	}
	result, err := client.Auth().SendCode(ctx, phone, auth.SendCodeOptions{})
	sent, ok := result.(*tg.AuthSentCode)
	if err != nil || !ok {
		stop()
		return "start", "Telegram could not send a code. Check the details and try later."
	}
	a.pending = &Pending{browser: browser, phone: phone, hash: sent.PhoneCodeHash, step: "code", apiID: id, apiHash: hash, client: client, storage: storage, cancel: stop, done: done, expires: time.Now().Add(5 * time.Minute)}
	return "code", "Enter the login code sent by Telegram."
}
func (a *App) verifySetup(ctx context.Context, r *http.Request) (string, string) {
	p := a.pending
	if p == nil {
		return "start", "Setup expired."
	}
	p.attempts++
	if p.attempts > 5 {
		a.closePending()
		return "start", "Too many attempts. Start again later."
	}
	var err error
	if p.step == "code" {
		_, err = p.client.Auth().SignIn(ctx, p.phone, r.Form.Get("code"), p.hash)
	} else {
		_, err = p.client.Auth().Password(ctx, r.Form.Get("password"))
	}
	if errors.Is(err, auth.ErrPasswordAuthNeeded) {
		p.step = "password"
		return "password", "Enter your Telegram two-step verification password."
	}
	if err != nil {
		if _, flood := tgerr.AsFloodWait(err); flood {
			a.closePending()
			return "start", "Telegram paused sign-in. Try again later."
		}
		return p.step, "Telegram rejected this value. Check it and retry."
	}
	me, err := p.client.Self(ctx)
	if err != nil || me.Bot || me.ID != a.config.Owner {
		a.closePending()
		return "start", "The Telegram account must match OWNER_ID."
	}
	loader := session.Loader{Storage: p.storage}
	data, err := loader.Load(ctx)
	if err != nil {
		return p.step, "Could not save the session. Try again."
	}
	encoded, err := encodeHydrogram(data, p.apiID, me.ID)
	if err != nil {
		return p.step, "Could not save the session. Try again."
	}
	payload, _ := json.Marshal([]any{p.apiID, p.apiHash, encoded})
	encrypted, err := a.seal(payload)
	if err == nil {
		err = a.store.set(ctx, "worker_repair", a.config.Owner, bson.M{"credentials": bson.Binary{Subtype: 0, Data: encrypted}})
	}
	if err != nil {
		return p.step, "Could not store the session securely. Try again."
	}
	a.closePending()
	return "done", "Repair account connected. Run /workerrepair to add confirmed missing workers."
}
func (a *App) cipher() (cipher.AEAD, error) {
	mac := hmac.New(sha256.New, []byte(a.config.Token))
	fmt.Fprintf(mac, "GateStream worker repair v1:%d", a.config.Owner)
	block, err := aes.NewCipher(mac.Sum(nil))
	if err != nil {
		return nil, err
	}
	return cipher.NewGCM(block)
}
func (a *App) seal(value []byte) ([]byte, error) {
	box, err := a.cipher()
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, 12)
	if _, err = rand.Read(nonce); err != nil {
		return nil, err
	}
	return box.Seal(nonce, nonce, value, nil), nil
}
func (a *App) credentials(ctx context.Context) (int, string, string, error) {
	record, err := a.store.find(ctx, "worker_repair", bson.M{"_id": a.config.Owner})
	if err != nil && !isNotFound(err) {
		return 0, "", "", err
	}
	var encrypted []byte
	switch v := record["credentials"].(type) {
	case bson.Binary:
		encrypted = v.Data
	case []byte:
		encrypted = v
	}
	if len(encrypted) > 12 {
		box, err := a.cipher()
		if err != nil {
			return 0, "", "", err
		}
		plain, err := box.Open(nil, encrypted[:12], encrypted[12:], nil)
		if err != nil {
			return 0, "", "", errors.New("saved repair session could not be opened")
		}
		var fields []json.RawMessage
		if json.Unmarshal(plain, &fields) != nil || len(fields) != 3 {
			return 0, "", "", errors.New("invalid saved session")
		}
		var id int
		var hash, token string
		if json.Unmarshal(fields[0], &id) != nil || json.Unmarshal(fields[1], &hash) != nil || json.Unmarshal(fields[2], &token) != nil {
			return 0, "", "", errors.New("invalid saved session")
		}
		return id, hash, token, nil
	}
	if a.config.RepairID > 0 && a.config.RepairHash != "" && a.config.RepairSession != "" {
		return a.config.RepairID, a.config.RepairHash, a.config.RepairSession, nil
	}
	return 0, "", "", errors.New("run /workersetup to connect the owner account")
}
func encodeHydrogram(data *session.Data, appID int, uid int64) (string, error) {
	if len(data.AuthKey) != 256 {
		return "", errors.New("invalid Telegram authorization key")
	}
	raw := make([]byte, 271)
	raw[0] = byte(data.DC)
	binary.BigEndian.PutUint32(raw[1:5], uint32(appID))
	copy(raw[6:262], data.AuthKey)
	binary.BigEndian.PutUint64(raw[262:270], uint64(uid))
	return base64.RawURLEncoding.EncodeToString(raw), nil
}
func decodeHydrogram(token string) (*session.Data, error) {
	raw, err := base64.RawURLEncoding.DecodeString(strings.TrimRight(token, "="))
	if err != nil || len(raw) != 271 || raw[5] != 0 || raw[270] != 0 {
		return nil, errors.New("unsupported repair session format")
	}
	dc := int(raw[0])
	var addr string
	for _, option := range dcs.Prod().Options {
		if option.ID == dc && !option.Ipv6 && !option.MediaOnly {
			addr = fmt.Sprintf("%s:%d", option.IPAddress, option.Port)
			break
		}
	}
	if addr == "" {
		return nil, errors.New("unknown Telegram data center")
	}
	key := append([]byte{}, raw[6:262]...)
	hash := sha1.Sum(key)
	return &session.Data{DC: dc, Addr: addr, AuthKey: key, AuthKeyID: hash[12:], Config: session.Config{ThisDC: dc}}, nil
}

func (a *App) repairChannelID(ctx context.Context, args []string) (int64, error) {
	channel := a.config.Channel
	if len(args) > 1 {
		return 0, errors.New("Use /workers CHANNEL_ID or /workerrepair CHANNEL_ID")
	}
	if len(args) == 1 {
		var err error
		channel, err = strconv.ParseInt(args[0], 10, 64)
		if err != nil {
			return 0, errors.New("Use a numeric storage channel ID")
		}
	}
	if channel >= 0 {
		return 0, errors.New("Set TELEGRAM_CHANNEL_ID or choose an existing storage channel")
	}
	if channel == a.config.Channel {
		return channel, nil
	}
	for _, name := range fileCollections {
		_, err := a.store.find(ctx, name, bson.M{"channel_id": channel})
		if err == nil {
			return channel, nil
		}
		if !isNotFound(err) {
			return 0, errors.New("Could not verify storage channel")
		}
	}
	return 0, errors.New("Choose a channel already used by GateStream")
}
func (a *App) launchRepair(ctx context.Context, m Message, args []string) {
	channel, err := a.repairChannelID(ctx, args)
	if err != nil {
		a.reply(ctx, m, err.Error(), nil)
		return
	}
	if !a.repairMu.TryLock() {
		a.reply(ctx, m, "Worker repair is already running.", nil)
		return
	}
	a.reply(ctx, m, "Worker repair started. Confirmed missing bots will be added with at least 60 seconds between attempts.", nil)
	a.wg.Add(1)
	go func() {
		defer a.wg.Done()
		defer a.repairMu.Unlock()
		text := a.repair(a.ctx, channel)
		if a.ctx.Err() == nil {
			a.reply(a.ctx, m, html.EscapeString(text), nil)
		}
	}()
}
func (a *App) repair(ctx context.Context, channel int64) string {
	id, hash, token, err := a.credentials(ctx)
	if err != nil {
		return err.Error()
	}
	data, err := decodeHydrogram(token)
	if err != nil {
		return "Saved session format could not be read. Run /workersetup."
	}
	storage := &session.StorageMemory{}
	loader := session.Loader{Storage: storage}
	if loader.Save(ctx, data) != nil {
		return "Could not open repair session."
	}
	client, stop, done, err := connectedUser(ctx, id, hash, storage)
	if err != nil {
		return "Could not connect the repair account."
	}
	defer func() { stop(); <-done }()
	me, err := client.Self(ctx)
	if err != nil || me.Bot || me.ID != a.config.Owner {
		return "The repair session must belong to OWNER_ID. Run /workersetup."
	}
	w := &Worker{client: client, peers: map[int64]Peer{}}
	// Portable sessions carry no peer table. Resolve the owner's recent dialogs.
	result, err := client.API().MessagesGetDialogs(ctx, &tg.MessagesGetDialogsRequest{OffsetPeer: &tg.InputPeerEmpty{}, Limit: 100})
	if err == nil {
		if chats, ok := result.(interface{ GetChats() []tg.ChatClass }); ok {
			w.remember(chats.GetChats())
		}
	}
	peer, err := w.channel(ctx, channel)
	if err != nil {
		return "The owner account cannot resolve the channel. Open it in Telegram and retry."
	}
	own, err := client.API().ChannelsGetParticipant(ctx, &tg.ChannelsGetParticipantRequest{Channel: peer, Participant: &tg.InputPeerSelf{}})
	if err != nil {
		return "Could not verify owner channel permissions."
	}
	authorized := false
	switch participant := own.Participant.(type) {
	case *tg.ChannelParticipantCreator:
		authorized = true
	case *tg.ChannelParticipantAdmin:
		authorized = participant.AdminRights.AddAdmins
	}
	if !authorized {
		return "The owner account needs permission to add channel administrators."
	}
	added, present, blocked := 0, 0, 0
	for _, worker := range a.workers[1:] {
		if ctx.Err() != nil {
			return "Worker repair stopped."
		}
		resolved, err := client.API().ContactsResolveUsername(ctx, &tg.ContactsResolveUsernameRequest{Username: worker.api.Me.Username})
		if err != nil {
			return "Could not verify a worker identity. Repair paused."
		}
		var user *tg.User
		for _, item := range resolved.Users {
			if candidate, ok := item.(*tg.User); ok && candidate.ID == worker.api.Me.ID && candidate.Bot {
				user = candidate
			}
		}
		if user == nil {
			return "Worker identity did not match. Repair paused."
		}
		participant, err := client.API().ChannelsGetParticipant(ctx, &tg.ChannelsGetParticipantRequest{Channel: peer, Participant: &tg.InputPeerUser{UserID: user.ID, AccessHash: user.AccessHash}})
		missing := tgerr.Is(err, "USER_NOT_PARTICIPANT")
		if err != nil && !missing {
			return "Membership could not be verified. Repair paused."
		}
		if err == nil {
			switch participant.Participant.(type) {
			case *tg.ChannelParticipantBanned:
				blocked++
				continue
			case *tg.ChannelParticipantLeft:
				missing = true
			default:
				present++
				continue
			}
		}
		if !missing {
			continue
		}
		state, err := a.store.find(ctx, "worker_repair", bson.M{"_id": a.config.Owner})
		if err != nil {
			return "Could not read repair pacing. Repair paused."
		}
		next := time.Unix(0, int64(floatValue(state["next_at"])*float64(time.Second)))
		if wait := time.Until(next); wait > 0 {
			select {
			case <-time.After(wait):
			case <-ctx.Done():
				return "Worker repair stopped."
			}
		}
		until := repairDeadline(time.Now(), 60*time.Second)
		_, err = a.store.db.Collection("worker_repair").UpdateOne(ctx, bson.M{"_id": a.config.Owner}, bson.M{"$max": bson.M{"next_at": until}}, options.UpdateOne().SetUpsert(true))
		if err != nil {
			return "Could not persist pacing. Repair paused."
		}
		_, err = client.API().ChannelsEditAdmin(ctx, &tg.ChannelsEditAdminRequest{Channel: peer, UserID: &tg.InputUser{UserID: user.ID, AccessHash: user.AccessHash}, AdminRights: tg.ChatAdminRights{Other: true}})
		if err != nil {
			if wait, ok := tgerr.AsFloodWait(err); ok {
				_, _ = a.store.db.Collection("worker_repair").UpdateOne(ctx, bson.M{"_id": a.config.Owner}, bson.M{"$max": bson.M{"next_at": repairDeadline(time.Now(), wait+time.Second)}})
			}
			return "Telegram refused an addition. Repair paused. Check account limits and permissions before retrying."
		}
		added++
	}
	return fmt.Sprintf("Worker repair finished. Added %d · Already present %d · Blocked %d.", added, present, blocked)
}
func floatValue(value any) float64 {
	if v, ok := value.(float64); ok {
		return v
	}
	return float64(integerValue(value))
}

func repairDeadline(now time.Time, wait time.Duration) float64 {
	return float64(now.Add(wait).UnixMilli()+1) / 1000
}
