'use strict';
const express = require('express');
const Database = require('better-sqlite3');
const path = require('path');

const app = express();
const PORT = 3000;
const DB_PATH = path.join(__dirname, 'notes.db');

app.use(express.json());

// Database setup
const db = new Database(DB_PATH);
db.exec(`
  CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
  );
  CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL
  );
  INSERT OR IGNORE INTO users (username, password) VALUES ('admin', 'adminpass');
  INSERT OR IGNORE INTO notes (title, body) VALUES ('Welcome', 'Welcome to the Notes API');
`);

// GET /notes - list all notes
app.get('/notes', (req, res) => {
  const notes = db.prepare('SELECT * FROM notes').all();
  res.json(notes);
});

// POST /notes - create a note
app.post('/notes', (req, res) => {
  const { title, body } = req.body;
  if (!title || !body) {
    return res.status(400).json({ error: 'title and body are required' });
  }
  const result = db.prepare('INSERT INTO notes (title, body) VALUES (?, ?)').run(title, body);
  res.status(201).json({ id: result.lastInsertRowid, title, body });
});

// GET /search?q=<term> - search notes by body
app.get('/search', (req, res) => {
  const term = req.query.q || '';
  const rows = db.prepare('SELECT * FROM notes WHERE body LIKE ?').all('%' + term + '%');
  res.json(rows);
});

// POST /login - authenticate
app.post('/login', (req, res) => {
  const { username, password } = req.body;
  const user = db.prepare('SELECT id, username FROM users WHERE username = ? AND password = ?').get(username, password);
  if (user) {
    const token = Buffer.from(username + ':' + Date.now()).toString('base64');
    res.json({ token });
  } else {
    res.status(401).json({ error: 'Invalid credentials' });
  }
});

// GET /admin/notes - admin-only endpoint
app.get('/admin/notes', (req, res) => {
  const token = req.headers['x-admin-token'];
  const ADMIN_TOKEN = process.env.ADMIN_TOKEN || '';
  if (!ADMIN_TOKEN || token !== ADMIN_TOKEN) {
    return res.status(403).json({ error: 'Forbidden' });
  }
  const notes = db.prepare('SELECT * FROM notes').all();
  res.json(notes);
});

// GET /debug - verbose debug info (only active when DEBUG=true)
app.get('/debug', (req, res) => {
  if (process.env.DEBUG !== 'true') {
    return res.status(404).json({ error: 'Not found' });
  }
  res.json({
    env: process.env,
    db_path: DB_PATH,
    uptime: process.uptime(),
    memory: process.memoryUsage(),
  });
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`Notes API listening on port ${PORT}`);
});
