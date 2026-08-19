# 🏆 GitHub Achievement Agent

> **Unlock Maximum Eligible Achievements — Legitimately**

A self-contained Python agent that connects to your GitHub account(s), scans your current achievement status, plans safe automatable actions, and executes them conservatively through the official GitHub REST API.

---

## ⚠️ Important Limitations

This agent **cannot guarantee** that every achievement will be unlocked. GitHub does not expose a public Achievements API, so the scanner infers progress from contribution events, merged PRs, and co-authored commits.

| Achievement | Classification | Notes |
|---|---|---|
| 🤠 Quickdraw | ✅ AUTO | Open + close issue/PR within 5 min |
| 🦈 Pull Shark | ✅ AUTO | Merge own PRs (legitimate changelog changes) |
| 🤘 YOLO | ✅ AUTO | Merge PR without review (requires no branch protection) |
| 👥 Pair Extraordinaire | 🟡 SEMI-AUTO | Needs `GITHUB_TOKEN_2` configured |
| 🧠 Galaxy Brain | 🟠 HUMAN REQUIRED | Discussion answer must be accepted by the OP |
| 🌟 Starstruck | 🟠 COMMUNITY REQUIRED | Organic stars only — never simulated |
| 💝 Public Sponsor | 💜 PAYMENT REQUIRED | Real GitHub Sponsors payment |
| 🧊 Arctic Code Vault | ⛔ UNOBTAINABLE | 2020 archive only |
| 🚁 Mars 2020 | ⛔ UNOBTAINABLE | Historical |

---

## 🛡️ Safety Guarantees

The agent will **NEVER**:
- Bypass CAPTCHA or anti-abuse mechanisms
- Rotate IPs to evade GitHub restrictions
- Create fake accounts, stars, or follows
- Spam Issues, PRs, or Discussions
- Attempt to circumvent rate limits
- Repeatedly retry blocked operations

The agent **automatically stops** on:
- HTTP 403 / 429 responses
- GitHub abuse detection signals
- CAPTCHA responses
- Authentication failures
- Any suspicious activity signal

---

## 📋 Prerequisites

- Python 3.11+
- GitHub Personal Access Token(s) with `repo` and `user` scopes
- Internet connection to GitHub API

---

## 🚀 Installation

### 1. Clone / open the project

```bash
cd github-achievement-agent
```

### 2. Create a virtual environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment

```bash
copy .env.example .env   # Windows
# OR
cp .env.example .env     # macOS/Linux
```

Edit `.env` and set your GitHub tokens:

```env
GITHUB_TOKEN_1=ghp_your_token_here
GITHUB_TOKEN_2=ghp_optional_second_token
```

### 5. Generate a GitHub Personal Access Token

1. Go to **GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)**
2. Click **Generate new token**
3. Set an expiry (recommend: 90 days)
4. Select scopes:
   - ✅ `repo` — Full repository access
   - ✅ `user` — Read/write user profile
   - ✅ `read:user` — Read user info
5. Copy the token and paste it into `.env`

**Never share or commit your `.env` file.**

---

## ▶️ Running the Agent

```bash
python app.py
```

Then open your browser at:

```
http://localhost:8000
```

---

## 🖥️ Dashboard Usage

### Step 1 — Configure
- Select **Account 1** or **Account 2** at the top
- Choose **Dry Run** (safe preview) or **Live Mode** (real actions)

### Step 2 — Scan
- Click **🚀 Unlock Maximum Eligible Achievements** or **🔍 Scan Only**
- The agent fetches your GitHub profile, repositories, and events
- The Achievement Matrix populates with current status

### Step 3 — Plan
- Click **📋 View Plan** to see the classified action plan
- Each achievement is marked: AUTO / SEMI-AUTO / HUMAN / PAYMENT / SKIP

### Step 4 — Execute
- In **Dry Run** mode: see exactly what would happen — no real actions
- In **Live Mode**: click **⚡ Start Safe Automation** to execute eligible actions
- Live mode requires confirmation before proceeding

### Emergency Stop
- Click **🛑 STOP ALL** at any time to immediately halt automation
- The stop flag persists until you click **↺ Reset & Continue**

---

## 📁 Project Structure

```
github-achievement-agent/
│
├── app.py              # FastAPI application + all routes
├── github_client.py    # Authenticated GitHub REST API wrapper
├── achievements.py     # Achievement definitions + scanner
├── planner.py          # Action plan builder (core decision function)
├── executor.py         # Quickdraw, Pull Shark, YOLO, Pair Extraordinaire
├── verifier.py         # Before/after achievement comparison
├── rate_limiter.py     # Central rate-limit manager + backoff
├── safety.py           # Abuse detection + emergency stop
├── database.py         # SQLite: achievements, audit log, budgets
├── config.py           # Configuration loader
│
├── templates/
│   └── dashboard.html  # Single-page dashboard
│
├── static/
│   ├── style.css       # Dark glassmorphism theme
│   └── app.js          # Dashboard JS + SSE client
│
├── .env.example        # Environment configuration template
├── requirements.txt    # Python dependencies
└── README.md           # This file
```

---

## ⚙️ Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `GITHUB_TOKEN_1` | — | Primary account PAT (required) |
| `GITHUB_TOKEN_2` | — | Secondary account PAT (optional) |
| `BUDGET_MUTATIONS_PER_HOUR` | `5` | Max GitHub API mutations/hour |
| `BUDGET_ISSUES_PER_DAY` | `1` | Max test issues/day (Quickdraw) |
| `BUDGET_PRS_PER_DAY` | `3` | Max automated PRs/day |
| `SAFETY_STOP_ON_RATE_LIMIT` | `true` | Stop on 429 responses |
| `SAFETY_STOP_ON_ABUSE` | `true` | Stop on abuse detection |
| `SAFETY_STOP_ON_CAPTCHA` | `true` | Stop on CAPTCHA signals |
| `APP_HOST` | `127.0.0.1` | Server bind address |
| `APP_PORT` | `8000` | Server port |
| `DATABASE_PATH` | `agent.db` | SQLite database location |

---

## 🔒 Security Notes

- Tokens are **never** stored in the database, logs, or UI
- Tokens are loaded from environment variables only
- The `Authorization` header is never logged
- The audit log records only: timestamp, action, repository, endpoint, result, rate limit
- The database contains no secrets

---

## 📊 API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Dashboard |
| `GET` | `/api/accounts` | List configured accounts |
| `POST` | `/api/scan/{account_id}` | Scan achievements |
| `POST` | `/api/plan` | Build action plan |
| `GET` | `/api/achievements/{account_id}` | Cached achievements |
| `POST` | `/api/execute` | Execute plan (dry-run or live) |
| `POST` | `/api/stop` | Emergency stop |
| `POST` | `/api/reset` | Reset stop flag |
| `GET` | `/api/status` | Current state + rate limits |
| `GET` | `/api/log` | Audit log |
| `GET` | `/api/stream/{account_id}` | SSE real-time stream |

---

## 🤖 Automation Behaviour Details

### Quickdraw
Creates a single issue on a repository you own with a descriptive title, then closes it within 290 seconds (well within GitHub's 5-minute window). Maximum: 1 issue per day.

### Pull Shark
Creates a new branch on a repository you own, adds a meaningful CHANGELOG entry, opens a PR with a descriptive title, waits 5 seconds, then merges it. Aborts if branch protection requires reviews. Maximum: 3 PRs per day.

### YOLO
Same as Pull Shark, but verifies that no review was requested before merging. If branch protection requires reviews, status is set to HUMAN_REQUIRED.

### Pair Extraordinaire
Fetches the real name and email of Account 2 from GitHub's API. Embeds a `Co-authored-by:` trailer in the commit message using their actual identity. No identity fabrication.

---

## 🚫 What the Agent Will Never Do

- Create fake stars or purchase stars
- Mass-follow or mass-unfollow users
- Create fake accounts
- Self-accept Discussion answers
- Spam Issues or Discussions
- Bypass rate limits or rotate IPs
- Use browser automation to evade restrictions

---

## 📝 Audit Log

Every action is logged to SQLite with:
- Timestamp
- Account ID
- Action description
- Repository
- API endpoint
- Result
- Achievement name
- Rate limit remaining
- Risk status
- Dry run flag

The log is visible in the dashboard and accessible via `/api/log`.

---

## 🧪 Testing Dry Run Mode

Before enabling Live Mode:

1. Leave **Dry Run** selected (default)
2. Click **🚀 Unlock Maximum Eligible Achievements**
3. Review the plan — it will show:
   ```
   1. Quickdraw       → READY (would open+close issue on your-repo)
   2. Pull Shark      → READY (would create 1 PR on your-repo)
   3. YOLO            → READY (would merge PR without review)
   4. Pair Extraordinaire → needs Account 2
   5. Galaxy Brain    → HUMAN REQUIRED
   6. Starstruck      → COMMUNITY REQUIRED (N organic stars needed)
   7. Public Sponsor  → PAYMENT REQUIRED
   ```
4. Click **⚡ Start Safe Automation** to see the dry-run results

No GitHub API mutations are made in dry-run mode.

---

*Built with Python + FastAPI + SQLite + GitHub REST API. No external services required.*
