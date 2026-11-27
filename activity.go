package main

import (
	"context"
	"encoding/json"
	"go.mongodb.org/mongo-driver/v2/bson"
	"sort"
	"sync"
	"time"
)

type interval struct{ Start, Stop int64 }
type coverage struct {
	Ranges   []interval
	Seen     time.Time
	Complete bool
}
type transfer struct {
	Stop    func()             `json:"-"`
	ID      int64              `json:"id"`
	Kind    string             `json:"kind"`
	Done    int64              `json:"bytes"`
	Total   int64              `json:"total_bytes"`
	Started time.Time          `json:"-"`
	Cancel  context.CancelFunc `json:"-"`
	Key     string             `json:"-"`
}
type Activity struct {
	mu               sync.Mutex
	started          time.Time
	serial           int64
	totals           map[string]bson.M
	jobs             map[int64]*transfer
	coverage         map[string]*coverage
	traffic          [6]int64
	seconds          [6]int64
	state, lastSaved string
	restored         bool
	lastCheckpoint   string
	flushMu          sync.Mutex
}

func newActivity() *Activity {
	a := &Activity{started: time.Now(), totals: map[string]bson.M{}, jobs: map[int64]*transfer{}, coverage: map[string]*coverage{}, state: "pending"}
	for _, kind := range []string{"media", "download", "upload"} {
		a.totals[kind] = bson.M{"bytes": int64(0), "completed": int64(0), "failed": int64(0), "incomplete": int64(0)}
	}
	a.totals["media"]["single_downloads"] = int64(0)
	return a
}
func mergeRanges(ranges []interval, add interval) []interval {
	ranges = append(ranges, add)
	sort.Slice(ranges, func(i, j int) bool { return ranges[i].Start < ranges[j].Start })
	var merged []interval
	for _, v := range ranges {
		if len(merged) > 0 && v.Start <= merged[len(merged)-1].Stop {
			merged[len(merged)-1].Stop = max(merged[len(merged)-1].Stop, v.Stop)
		} else {
			merged = append(merged, v)
		}
	}
	return merged
}
func (a *Activity) begin(ctx context.Context, total int64, key string) (context.Context, int64) {
	ctx, cancel := context.WithCancel(ctx)
	a.mu.Lock()
	defer a.mu.Unlock()
	a.serial++
	a.jobs[a.serial] = &transfer{ID: a.serial, Kind: "media", Total: total, Started: time.Now(), Key: key, Cancel: cancel}
	return ctx, a.serial
}
func (a *Activity) progress(id, n int64, key string, start, size int64, count bool) {
	a.mu.Lock()
	defer a.mu.Unlock()
	job := a.jobs[id]
	if job == nil {
		return
	}
	job.Done += n
	totals := a.totals["media"]
	totals["bytes"] = num(totals, "bytes") + n
	second := time.Now().Unix()
	bucket := second % 6
	if a.seconds[bucket] != second {
		a.seconds[bucket] = second
		a.traffic[bucket] = 0
	}
	a.traffic[bucket] += n
	if !count || n <= 0 {
		return
	}
	now := time.Now()
	entry := a.coverage[key]
	if entry == nil {
		if len(a.coverage) >= 1024 {
			var oldest string
			var seen time.Time
			for k, v := range a.coverage {
				if oldest == "" || v.Seen.Before(seen) {
					oldest = k
					seen = v.Seen
				}
			}
			delete(a.coverage, oldest)
		}
		entry = &coverage{}
		a.coverage[key] = entry
	}
	if entry.Complete {
		if start != 0 || now.Sub(entry.Seen) < 10*time.Second {
			entry.Seen = now
			return
		}
		entry.Ranges = nil
		entry.Complete = false
	}
	entry.Seen = now
	entry.Ranges = mergeRanges(entry.Ranges, interval{max(0, start), min(size, start+n)})
	if len(entry.Ranges) == 1 && entry.Ranges[0] == (interval{0, size}) {
		totals["single_downloads"] = num(totals, "single_downloads") + 1
		entry.Ranges = nil
		entry.Complete = true
	}
	if len(entry.Ranges) > 256 {
		delete(a.coverage, key)
	}
}
func (a *Activity) finish(id int64, failed bool) {
	a.mu.Lock()
	defer a.mu.Unlock()
	job := a.jobs[id]
	if job == nil {
		return
	}
	job.Cancel()
	delete(a.jobs, id)
	outcome := "incomplete"
	if job.Done == job.Total {
		outcome = "completed"
	} else if failed {
		outcome = "failed"
	}
	a.totals["media"][outcome] = num(a.totals["media"], outcome) + 1
}
func (a *Activity) cancel(id int64) bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	if job := a.jobs[id]; job != nil {
		job.Cancel()
		if job.Stop != nil {
			job.Stop()
		}
		return true
	}
	return false
}
func (a *Activity) checkpointLocked() bson.M {
	now := time.Now()
	for k, v := range a.coverage {
		if now.Sub(v.Seen) > 24*time.Hour {
			delete(a.coverage, k)
		}
	}
	phases := bson.M{}
	for kind, totals := range a.totals {
		values := bson.M{}
		for k, v := range totals {
			values[k] = v
		}
		values["active"] = int64(0)
		if kind == "media" {
			values["active"] = int64(len(a.jobs))
		}
		phases[kind] = values
	}
	return phases
}
func (a *Activity) snapshot() map[string]any {
	a.mu.Lock()
	defer a.mu.Unlock()
	phases := a.checkpointLocked()
	var traffic int64
	second := time.Now().Unix()
	for i, t := range a.seconds {
		if t > second-5 {
			traffic += a.traffic[i]
		}
	}
	for kind, value := range phases {
		speed := float64(0)
		if kind == "media" {
			speed = float64(traffic) / min(5, max(1, time.Since(a.started).Seconds()))
		}
		value.(bson.M)["speed_bps"] = speed
	}
	var transfers []map[string]any
	keys := map[string]bool{}
	ids := make([]int64, 0, len(a.jobs))
	for id, job := range a.jobs {
		ids = append(ids, id)
		keys[job.Key] = true
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] > ids[j] })
	for _, id := range ids[:min(20, len(ids))] {
		job := a.jobs[id]
		elapsed := max(.001, time.Since(job.Started).Seconds())
		speed := float64(job.Done) / elapsed
		transfers = append(transfers, map[string]any{"id": id, "kind": "media", "bytes": job.Done, "total_bytes": job.Total, "elapsed_seconds": elapsed, "speed_bps": speed, "percent": 100 * float64(job.Done) / float64(max(1, job.Total)), "eta_seconds": float64(job.Total-job.Done) / max(1, speed)})
	}
	return map[string]any{"uptime_seconds": time.Since(a.started).Seconds(), "phases": phases, "persistence": map[string]any{"state": a.state, "last_saved_at": a.lastSaved}, "active_media_links": len(keys), "transfers": transfers, "hidden_transfers": max(0, len(a.jobs)-20)}
}
func (a *Activity) flush(ctx context.Context, s *Store) error {
	a.flushMu.Lock()
	defer a.flushMu.Unlock()
	a.mu.Lock()
	restored := a.restored
	a.mu.Unlock()
	if !restored {
		doc, err := s.find(ctx, "analytics", bson.M{"_id": "transfer_activity"})
		if err != nil && !isNotFound(err) {
			return a.storeError(err)
		}
		phases, _ := doc["phases"].(bson.M)
		if doc == nil {
			legacy, err := s.find(ctx, "analytics", bson.M{"_id": "bandwidth"})
			if err != nil && !isNotFound(err) {
				return a.storeError(err)
			}
			phases = bson.M{"media": bson.M{"bytes": num(legacy, "bytes_streamed")}}
		}
		a.mu.Lock()
		for kind, totals := range a.totals {
			old, _ := phases[kind].(bson.M)
			for k := range totals {
				totals[k] = num(totals, k) + max(0, num(old, k))
			}
			totals["incomplete"] = num(totals, "incomplete") + max(0, num(old, "active"))
		}
		a.restored = true
		a.mu.Unlock()
	}
	a.mu.Lock()
	phases := a.checkpointLocked()
	a.mu.Unlock()
	encoded, _ := json.Marshal(phases)
	if string(encoded) != a.lastCheckpoint {
		updates := bson.M{"updated_at": time.Now()}
		for kind, value := range phases {
			for key, v := range value.(bson.M) {
				updates["phases."+kind+"."+key] = v
			}
		}
		if err := s.set(ctx, "analytics", "transfer_activity", updates); err != nil {
			return a.storeError(err)
		}
		a.lastCheckpoint = string(encoded)
	}
	a.mu.Lock()
	a.state = "saved"
	a.lastSaved = time.Now().UTC().Format(time.RFC3339)
	a.mu.Unlock()
	return nil
}
func (a *Activity) storeError(err error) error {
	a.mu.Lock()
	a.state = "unavailable"
	a.mu.Unlock()
	return err
}

func (a *Activity) setStop(id int64, stop func()) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if job := a.jobs[id]; job != nil {
		job.Stop = stop
	}
}
