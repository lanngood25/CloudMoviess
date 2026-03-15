# 🎬 CloudMovies

Website streaming film dengan backend Python + MovieBox API.

---

## 🚀 Cara Jalankan

### Cara Cepat (Rekomendasi)
```bash
python start.py
```
Script ini akan:
1. Install semua dependensi otomatis
2. Jalankan backend di `http://localhost:8000`
3. Buka browser ke `index.html`

---

### Cara Manual

**1. Install dependensi:**
```bash
pip install -r requirements.txt
```

**2. Jalankan backend:**
```bash
uvicorn backend:app --host 0.0.0.0 --port 8000 --reload
```

**3. Buka frontend:**
Buka file `index.html` di browser.

---

## 📁 Struktur File

```
cloudmovies/
├── backend.py       ← FastAPI server (Python)
├── index.html       ← Frontend website
├── start.py         ← Script auto-start
├── requirements.txt ← Dependensi Python
└── README.md
```

## ✨ Fitur

- 🔍 **Search** — Cari movie/series dengan autocomplete
- 🎬 **Streaming** — Player built-in dengan HLS support
- 📺 **Pilih Resolusi** — 360p / 480p / 720p / 1080p
- 📝 **Subtitle** — Load & sync subtitle SRT real-time
- ⬇️ **Download** — Download film per kualitas
- 💬 **Komentar** — Komentar & like (disimpan lokal)
- 📌 **Watchlist** — Simpan film favorit

## 🔧 API Endpoints

| Endpoint | Method | Keterangan |
|----------|--------|------------|
| `/api/health` | GET | Cek status server |
| `/api/trending` | GET | Film trending |
| `/api/hot` | GET | Film & series panas |
| `/api/search` | POST | Cari film/series |
| `/api/suggest?q=` | GET | Autocomplete pencarian |
| `/api/stream/{id}` | GET | URL streaming |
| `/api/download/{id}` | GET | URL download |
| `/api/subtitle-proxy?url=` | GET | Proxy subtitle |

## ⚠️ Catatan

- Backend harus berjalan sebelum membuka `index.html`
- Koneksi internet diperlukan untuk mengakses MovieBox API
- Subtitle proxy di-handle oleh backend untuk menghindari CORS
