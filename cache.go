package main

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"syscall"
	"time"
)

type Cache struct {
	mu          sync.Mutex
	dir         string
	limit, free int64
	initialized bool
	entries     map[string]cacheEntry
	size        int64
}
type cacheEntry struct {
	size int64
	used time.Time
}

func (c *Cache) init() {
	if c.initialized {
		return
	}
	c.initialized = true
	c.entries = map[string]cacheEntry{}
	if os.MkdirAll(c.dir, 0700) != nil {
		return
	}
	entries, _ := os.ReadDir(c.dir)
	for _, entry := range entries {
		if !entry.IsDir() && strings.HasPrefix(entry.Name(), ".go-chunk-") {
			_ = os.Remove(filepath.Join(c.dir, entry.Name()))
			continue
		}
		legacy := regexp.MustCompile(`^[A-Za-z0-9_-]+_[0-9]+\.chunk$`).MatchString(entry.Name())
		if entry.IsDir() || ((!strings.HasPrefix(entry.Name(), "go-") || !strings.HasSuffix(entry.Name(), ".chunk")) && !legacy) {
			continue
		}
		info, err := entry.Info()
		if err == nil {
			c.entries[entry.Name()] = cacheEntry{info.Size(), info.ModTime()}
			c.size += info.Size()
		}
	}
	if c.limit > 0 {
		c.trim(0)
	}
}
func (c *Cache) trim(incoming int64) {
	var keys []string
	for key := range c.entries {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool { return c.entries[keys[i]].used.Before(c.entries[keys[j]].used) })
	for _, key := range keys {
		if c.size+incoming <= c.limit {
			break
		}
		if os.Remove(filepath.Join(c.dir, key)) == nil {
			c.size -= c.entries[key].size
			delete(c.entries, key)
		}
	}
}
func (c *Cache) get(key string) []byte {
	if c.limit == 0 {
		return nil
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	c.init()
	entry, ok := c.entries[key]
	if !ok {
		return nil
	}
	data, err := os.ReadFile(filepath.Join(c.dir, key))
	if err != nil || int64(len(data)) != entry.size {
		c.size -= entry.size
		delete(c.entries, key)
		return nil
	}
	entry.used = time.Now()
	c.entries[key] = entry
	return data
}
func (c *Cache) put(key string, data []byte) {
	if c.limit == 0 || int64(len(data)) > c.limit {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	c.init()
	if _, ok := c.entries[key]; ok {
		return
	}
	c.trim(int64(len(data)))
	if c.size+int64(len(data)) > c.limit {
		return
	}
	var stats syscall.Statfs_t
	if syscall.Statfs(c.dir, &stats) != nil || int64(stats.Bavail)*int64(stats.Bsize) < int64(len(data))+c.free {
		return
	}
	f, err := os.CreateTemp(c.dir, ".go-chunk-")
	if err != nil {
		return
	}
	path := f.Name()
	defer os.Remove(path)
	_, err = f.Write(data)
	closeErr := f.Close()
	if err != nil || closeErr != nil {
		return
	}
	if os.Rename(path, filepath.Join(c.dir, key)) != nil {
		return
	}
	c.entries[key] = cacheEntry{int64(len(data)), time.Now()}
	c.size += int64(len(data))
}
func (c *Cache) clear() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.init()
	count := 0
	for key, entry := range c.entries {
		if os.Remove(filepath.Join(c.dir, key)) == nil {
			count++
			c.size -= entry.size
			delete(c.entries, key)
		}
	}
	return count
}
