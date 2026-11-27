package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net/http"
	"net/url"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gotd/td/session"
	"github.com/gotd/td/telegram"
	"github.com/gotd/td/tg"
	"github.com/gotd/td/tgerr"
	_ "modernc.org/sqlite"
)

type User struct {
	ID       int64  `json:"id"`
	First    string `json:"first_name"`
	Last     string `json:"last_name"`
	Username string `json:"username"`
	Language string `json:"language_code"`
	Premium  bool   `json:"is_premium"`
	Bot      bool   `json:"is_bot"`
}
type Chat struct {
	ID    int64  `json:"id"`
	Type  string `json:"type"`
	Title string `json:"title"`
}
type Media struct {
	FileID   string `json:"file_id"`
	Name     string `json:"file_name"`
	Size     int64  `json:"file_size"`
	MIME     string `json:"mime_type"`
	Duration int    `json:"duration"`
}
type Message struct {
	ID                                       int      `json:"message_id"`
	Thread                                   int      `json:"message_thread_id"`
	From                                     User     `json:"from"`
	Chat                                     Chat     `json:"chat"`
	Text                                     string   `json:"text"`
	Caption                                  string   `json:"caption"`
	Reply                                    *Message `json:"reply_to_message"`
	Document, Video, Audio, Voice, VideoNote *Media
	Photo                                    []Media `json:"photo"`
	Forward                                  *struct {
		Type string `json:"type"`
		Chat Chat   `json:"chat"`
		ID   int    `json:"message_id"`
	} `json:"forward_origin"`
}

// Explicit tags are needed for Telegram's underscored media fields.
func (m *Message) UnmarshalJSON(data []byte) error {
	type plain Message
	var v struct {
		*plain
		VideoNote *Media `json:"video_note"`
	}
	v.plain = (*plain)(m)
	err := json.Unmarshal(data, &v)
	m.VideoNote = v.VideoNote
	return err
}
func (m Message) media() *Media {
	for _, v := range []*Media{m.Document, m.Video, m.Audio, m.Voice, m.VideoNote} {
		if v != nil {
			return v
		}
	}
	if len(m.Photo) > 0 {
		v := m.Photo[len(m.Photo)-1]
		v.Name = "photo.jpg"
		v.MIME = "image/jpeg"
		return &v
	}
	return nil
}
func (m Message) group() bool { return m.Chat.Type == "group" || m.Chat.Type == "supergroup" }

type APIError struct {
	Code int
	Wait int
}

func (e *APIError) Error() string { return fmt.Sprintf("Telegram API error %d", e.Code) }

type BotAPI struct {
	Token string
	HTTP  *http.Client
	Me    User
}

func (b *BotAPI) call(ctx context.Context, method string, input any, out any) error {
	data, err := json.Marshal(input)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, "POST", "https://api.telegram.org/bot"+b.Token+"/"+method, bytes.NewReader(data))
	if err != nil {
		return errors.New("Telegram request failed")
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := b.HTTP.Do(req)
	if err != nil {
		return errors.New("Telegram connection failed")
	}
	defer resp.Body.Close()
	var envelope struct {
		OK         bool            `json:"ok"`
		Result     json.RawMessage `json:"result"`
		Code       int             `json:"error_code"`
		Parameters struct {
			Wait int `json:"retry_after"`
		} `json:"parameters"`
	}
	if err = json.NewDecoder(io.LimitReader(resp.Body, 8<<20)).Decode(&envelope); err != nil {
		return errors.New("Telegram response failed")
	}
	if !envelope.OK {
		return &APIError{envelope.Code, envelope.Parameters.Wait}
	}
	if out != nil {
		return json.Unmarshal(envelope.Result, out)
	}
	return nil
}

type Peer struct {
	ID, Hash int64
	Username string
}
type mediaPools interface {
	Config() tg.Config
	Pool(int64) (telegram.CloseInvoker, error)
	DC(context.Context, int, int64) (telegram.CloseInvoker, error)
}

type Worker struct {
	media  mediaPools
	api    *BotAPI
	client *telegram.Client
	name   string
	ready  chan error
	done   chan struct{}
	mu     sync.Mutex
	peers  map[int64]Peer
	pools  map[int]telegram.CloseInvoker
	active atomic.Int64
	until  atomic.Int64
	stop   context.CancelFunc
	rpc    *tg.Client
}

func startWorker(ctx context.Context, c Config, index int, token, root string) (*Worker, error) {
	name := "bot_main"
	if index > 0 {
		name = "worker_" + strconv.Itoa(index)
	}
	w := &Worker{name: name, api: &BotAPI{Token: token, HTTP: &http.Client{Timeout: 40 * time.Second}}, ready: make(chan error, 1), done: make(chan struct{}), peers: map[int64]Peer{}, pools: map[int]telegram.CloseInvoker{}}
	authCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	if err := w.api.call(authCtx, "getMe", map[string]any{}, &w.api.Me); err != nil {
		return nil, err
	}
	w.importPeers(root)
	w.client = telegram.NewClient(c.APIID, c.APIHash, telegram.Options{NoUpdates: true, SessionStorage: &session.FileStorage{Path: filepath.Join(c.SessionDir, name+".json")}})
	w.rpc = w.client.API()
	w.media = w.client
	runCtx, stop := context.WithCancel(ctx)
	w.stop = stop
	go func() {
		defer close(w.done)
		err := w.client.Run(runCtx, func(inner context.Context) error {
			startup, closeStartup := context.WithTimeout(inner, 25*time.Second)
			_, err := w.client.Auth().Bot(startup, token)
			if err == nil {
				me, lookupErr := w.client.Self(startup)
				if lookupErr != nil || me.ID != w.api.Me.ID {
					err = errors.New("Telegram identity did not match the configured bot")
				}
			}
			closeStartup()
			w.ready <- err
			if err != nil {
				return err
			}
			<-inner.Done()
			return inner.Err()
		})
		select {
		case w.ready <- err:
		default:
		}
		w.mu.Lock()
		defer w.mu.Unlock()
		for _, pool := range w.pools {
			_ = pool.Close()
		}
	}()
	select {
	case err := <-w.ready:
		if err != nil {
			stop()
			return nil, errors.New("Telegram MTProto authentication failed")
		}
	case <-authCtx.Done():
		stop()
		return nil, errors.New("Telegram MTProto startup timed out")
	}
	return w, nil
}

func (w *Worker) importPeers(root string) {
	// Read previous Hydrogram peer hashes without changing the SQLite sessions.
	for _, path := range []string{filepath.Join(root, w.name+".session"), filepath.Join(root, "sessions", w.name+".session")} {
		db, err := sql.Open("sqlite", (&url.URL{Scheme: "file", Path: filepath.ToSlash(path), RawQuery: "mode=ro"}).String())
		if err != nil {
			continue
		}
		rows, err := db.Query("SELECT id, access_hash, username FROM peers WHERE id <= -1000000000000 LIMIT 512")
		if err == nil {
			for rows.Next() {
				var id, hash int64
				var username sql.NullString
				if rows.Scan(&id, &hash, &username) == nil {
					w.peers[id] = Peer{id, hash, username.String}
				}
			}
			_ = rows.Close()
		}
		_ = db.Close()
	}
}

func (w *Worker) remember(chats []tg.ChatClass) {
	w.mu.Lock()
	defer w.mu.Unlock()
	for _, chat := range chats {
		if ch, ok := chat.(*tg.Channel); ok {
			if len(w.peers) >= 512 {
				for id := range w.peers {
					delete(w.peers, id)
					break
				}
			}
			id := -1000000000000 - ch.ID
			w.peers[id] = Peer{ch.ID, ch.AccessHash, ch.Username}
		}
	}
}

func (w *Worker) channel(ctx context.Context, id int64) (*tg.InputChannel, error) {
	w.mu.Lock()
	p, ok := w.peers[id]
	w.mu.Unlock()
	if ok {
		return &tg.InputChannel{ChannelID: channelNumber(id), AccessHash: p.Hash}, nil
	}
	// Telegram can resolve channels the current account already participates in.
	result, err := w.rawAPI().ChannelsGetChannels(ctx, []tg.InputChannelClass{&tg.InputChannel{ChannelID: channelNumber(id)}})
	if err != nil {
		return nil, err
	}
	w.remember(result.GetChats())
	w.mu.Lock()
	p, ok = w.peers[id]
	w.mu.Unlock()
	if !ok {
		return nil, errors.New("channel is inaccessible to this worker")
	}
	return &tg.InputChannel{ChannelID: channelNumber(id), AccessHash: p.Hash}, nil
}
func channelNumber(id int64) int64 {
	if id <= -1000000000000 {
		return -id - 1000000000000
	}
	return id
}
func (w *Worker) resolveUsername(ctx context.Context, username string) (int64, error) {
	result, err := w.rawAPI().ContactsResolveUsername(ctx, &tg.ContactsResolveUsernameRequest{Username: username})
	if err != nil {
		return 0, err
	}
	w.remember(result.Chats)
	if ch, ok := result.Peer.(*tg.PeerChannel); ok {
		return -1000000000000 - ch.ChannelID, nil
	}
	return 0, errors.New("a Telegram channel or supergroup is required")
}

type Source struct {
	DC         int
	Location   tg.InputFileLocationClass
	Name, MIME string
	Size       int64
	Duration   int
}

func (w *Worker) source(ctx context.Context, channel int64, id int) (Source, error) {
	var result tg.MessagesMessagesClass
	var err error
	if channel > 0 {
		result, err = w.rawAPI().MessagesGetMessages(ctx, []tg.InputMessageClass{&tg.InputMessageID{ID: id}})
	} else {
		peer, resolveErr := w.channel(ctx, channel)
		if resolveErr != nil {
			return Source{}, resolveErr
		}
		result, err = w.rawAPI().ChannelsGetMessages(ctx, &tg.ChannelsGetMessagesRequest{Channel: peer, ID: []tg.InputMessageClass{&tg.InputMessageID{ID: id}}})
	}
	if err != nil {
		return Source{}, err
	}
	messages, ok := result.(interface{ GetMessages() []tg.MessageClass })
	if !ok {
		return Source{}, errors.New("Telegram message response missing")
	}
	for _, value := range messages.GetMessages() {
		m, ok := value.(*tg.Message)
		if !ok {
			continue
		}
		if media, ok := m.Media.(*tg.MessageMediaDocument); ok {
			if d, ok := media.Document.(*tg.Document); ok {
				s := Source{DC: d.DCID, Location: &tg.InputDocumentFileLocation{ID: d.ID, AccessHash: d.AccessHash, FileReference: d.FileReference}, Size: d.Size, MIME: d.MimeType}
				for _, a := range d.Attributes {
					switch attr := a.(type) {
					case *tg.DocumentAttributeFilename:
						s.Name = attr.FileName
					case *tg.DocumentAttributeVideo:
						s.Duration = int(attr.Duration)
					case *tg.DocumentAttributeAudio:
						s.Duration = attr.Duration
					}
				}
				if s.Name == "" {
					exts, _ := mime.ExtensionsByType(s.MIME)
					ext := ".bin"
					if len(exts) > 0 {
						ext = exts[0]
					}
					s.Name = "file_" + strconv.FormatInt(d.ID, 10) + ext
				}
				s.MIME = mediaMIME(s.Name, s.MIME)
				return s, nil
			}
		}
		if media, ok := m.Media.(*tg.MessageMediaPhoto); ok {
			if photo, ok := media.Photo.(*tg.Photo); ok {
				var size int
				var kind string
				for _, v := range photo.Sizes {
					switch p := v.(type) {
					case *tg.PhotoSize:
						if p.Size > size {
							size = p.Size
							kind = p.Type
						}
					case *tg.PhotoSizeProgressive:
						if len(p.Sizes) > 0 && p.Sizes[len(p.Sizes)-1] > size {
							size = p.Sizes[len(p.Sizes)-1]
							kind = p.Type
						}
					}
				}
				return Source{DC: photo.DCID, Location: &tg.InputPhotoFileLocation{ID: photo.ID, AccessHash: photo.AccessHash, FileReference: photo.FileReference, ThumbSize: kind}, Size: int64(size), MIME: "image/jpeg", Name: "photo_" + strconv.FormatInt(photo.ID, 10) + ".jpg"}, nil
			}
		}
	}
	return Source{}, errors.New("channel message contains no downloadable media")
}
func mediaMIME(name, value string) string {
	ext := strings.ToLower(filepath.Ext(name))
	if ext == ".apk" {
		return "application/vnd.android.package-archive"
	}
	if ext == ".mkv" {
		return "video/x-matroska"
	}
	if inferred := mime.TypeByExtension(ext); inferred != "" {
		return inferred
	}
	if value == "" {
		return "application/octet-stream"
	}
	return value
}

func (w *Worker) chunk(ctx context.Context, s Source, offset int64) ([]byte, error) {
	if w.until.Load() > time.Now().Unix() {
		return nil, errors.New("worker cooling down")
	}
	w.mu.Lock()
	pool := w.pools[s.DC]
	if pool == nil {
		var err error
		if s.DC == w.media.Config().ThisDC {
			pool, err = w.media.Pool(1)
		} else {
			pool, err = w.media.DC(ctx, s.DC, 1)
		}
		if err != nil {
			w.mu.Unlock()
			return nil, err
		}
		w.pools[s.DC] = pool
	}
	w.mu.Unlock()
	chunkCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	result, err := tg.NewClient(pool).UploadGetFile(chunkCtx, &tg.UploadGetFileRequest{Location: s.Location, Offset: offset, Limit: 1 << 20})
	if err != nil {
		if wait, ok := tgerr.AsFloodWait(err); ok {
			w.until.Store(time.Now().Add(wait + time.Second).Unix())
		}
		return nil, err
	}
	part, ok := result.(*tg.UploadFile)
	if !ok || len(part.Bytes) == 0 {
		return nil, io.ErrUnexpectedEOF
	}
	return part.Bytes, nil
}

func (w *Worker) rawAPI() *tg.Client {
	if w.rpc != nil {
		return w.rpc
	}
	return w.client.API()
}
