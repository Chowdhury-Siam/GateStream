package main

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"regexp"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

type Store struct {
	client *mongo.Client
	db     *mongo.Database
}

var storedBSONOptions = options.BSONOptions{DefaultDocumentM: true}

var fileCollections = []string{"movies", "direct_files", "anime", "webseries"}

func openStore(ctx context.Context, c Config) (*Store, error) {
	client, err := mongo.Connect(options.Client().SetBSONOptions(&storedBSONOptions).ApplyURI(c.MongoURI).SetMaxPoolSize(10).SetMinPoolSize(0).SetMaxConnIdleTime(time.Minute).SetServerSelectionTimeout(10 * time.Second))
	if err != nil {
		return nil, errors.New("MongoDB configuration failed")
	}
	if err = client.Ping(ctx, nil); err != nil {
		_ = client.Disconnect(ctx)
		return nil, errors.New("MongoDB connection failed")
	}
	s := &Store{client, client.Database(c.Database)}
	_, err = s.db.Collection("library_sessions").Indexes().CreateOne(ctx, mongo.IndexModel{Keys: bson.D{{Key: "expires_at", Value: 1}}, Options: options.Index().SetExpireAfterSeconds(0)})
	if err != nil {
		_ = client.Disconnect(ctx)
		return nil, errors.New("MongoDB session index failed")
	}
	return s, nil
}

func (s *Store) find(ctx context.Context, col string, query bson.M) (bson.M, error) {
	var doc bson.M
	err := s.db.Collection(col).FindOne(ctx, query).Decode(&doc)
	return doc, err
}
func (s *Store) set(ctx context.Context, col string, id any, fields bson.M) error {
	_, err := s.db.Collection(col).UpdateOne(ctx, bson.M{"_id": id}, bson.M{"$set": fields}, options.UpdateOne().SetUpsert(true))
	return err
}
func (s *Store) allowed(ctx context.Context, owner, uid, chat int64) bool {
	if uid <= 0 {
		return false
	}
	if uid == owner {
		return true
	}
	_, err := s.find(ctx, "bot_access", bson.M{"_id": bson.M{"$in": []int64{uid, chat}}})
	return err == nil
}
func (s *Store) File(ctx context.Context, code string) (bson.M, error) {
	if !regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`).MatchString(code) {
		return nil, mongo.ErrNoDocuments
	}
	for _, name := range fileCollections {
		doc, err := s.find(ctx, name, bson.M{"_id": code})
		if err == nil {
			return doc, nil
		}
		if !errors.Is(err, mongo.ErrNoDocuments) {
			return nil, err
		}
	}
	for _, name := range []string{"movies", "anime", "webseries"} {
		doc, err := s.find(ctx, name, bson.M{"episodes.code": code})
		if errors.Is(err, mongo.ErrNoDocuments) {
			continue
		}
		if err != nil {
			return nil, err
		}
		episodes, _ := doc["episodes"].(bson.A)
		for _, value := range episodes {
			ep, ok := value.(bson.M)
			if !ok || str(ep, "code") != code {
				continue
			}
			ep["_id"] = code
			ep["channel_id"] = doc["channel_id"]
			ep["user_id"] = doc["user_id"]
			ep["category"] = doc["category"]
			ep["batch_id"] = doc["_id"]
			return ep, nil
		}
	}
	return nil, mongo.ErrNoDocuments
}
func (s *Store) saveFile(ctx context.Context, c Config, doc bson.M, personal bool) (bson.M, error) {
	category := str(doc, "category")
	if category == "" {
		category = "direct_files"
	}
	section := "link"
	if personal {
		section = "personal"
	}
	col := s.db.Collection(category)
	filter := bson.M{"channel_id": doc["channel_id"], "message_id": doc["message_id"], "catalog_section": section}
	if !personal {
		filter["catalog_section"] = bson.M{"$nin": []string{"index", "personal"}}
	}
	var existing bson.M
	err := col.FindOne(ctx, filter).Decode(&existing)
	if err == nil {
		doc["_id"] = existing["_id"]
		doc["code"] = existing["code"]
		doc["created_at"] = existing["created_at"]
	} else if errors.Is(err, mongo.ErrNoDocuments) {
		code := randomToken(c.SecretLength)
		doc["_id"] = code
		doc["code"] = code
		doc["created_at"] = time.Now()
	} else {
		return nil, err
	}
	doc["catalog_section"] = section
	doc["category"] = category
	doc["updated_at"] = time.Now()
	fields := bson.M{}
	for k, v := range doc {
		if k != "_id" {
			fields[k] = v
		}
	}
	_, err = col.UpdateOne(ctx, bson.M{"_id": doc["_id"]}, bson.M{"$set": fields}, options.UpdateOne().SetUpsert(true))
	return doc, err
}
func (s *Store) revoke(ctx context.Context, code string) error {
	for _, name := range fileCollections {
		r, err := s.db.Collection(name).DeleteOne(ctx, bson.M{"_id": code})
		if err != nil {
			return err
		}
		if r.DeletedCount > 0 {
			return nil
		}
	}
	for _, name := range []string{"movies", "anime", "webseries"} {
		_, err := s.db.Collection(name).UpdateMany(ctx, bson.M{"episodes.code": code}, bson.M{"$pull": bson.M{"episodes": bson.M{"code": code}}})
		if err != nil {
			return err
		}
	}
	return nil
}
func digest(token string) string {
	sum := sha256.Sum256([]byte(token))
	return hex.EncodeToString(sum[:])
}
func (s *Store) newSession(ctx context.Context, uid int64, kind string, age time.Duration) (string, error) {
	raw := make([]byte, 32)
	tokenHex := randomToken(32)
	raw, _ = hex.DecodeString(tokenHex)
	token := base64.RawURLEncoding.EncodeToString(raw)
	_, err := s.db.Collection("library_sessions").InsertOne(ctx, bson.M{"_id": digest(token), "user_id": uid, "kind": kind, "expires_at": time.Now().Add(age)})
	return token, err
}
func (s *Store) session(ctx context.Context, token, kind string, consume bool) (bson.M, error) {
	if len(token) != 43 {
		return nil, mongo.ErrNoDocuments
	}
	q := bson.M{"_id": digest(token), "kind": kind, "expires_at": bson.M{"$gt": time.Now()}}
	if consume {
		var doc bson.M
		err := s.db.Collection("library_sessions").FindOneAndDelete(ctx, q).Decode(&doc)
		return doc, err
	}
	return s.find(ctx, "library_sessions", q)
}
func str(m bson.M, k string) string { v, _ := m[k].(string); return v }
func num(m bson.M, k string) int64  { return integerValue(m[k]) }
func integerValue(v any) int64 {
	switch n := v.(type) {
	case int:
		return int64(n)
	case int32:
		return int64(n)
	case int64:
		return n
	case float64:
		return int64(n)
	}
	return 0
}
