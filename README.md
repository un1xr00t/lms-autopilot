# Training Auto-Agent

An AI-powered browser agent that automatically completes online training courses for you. It watches videos, clicks through interactive slides, answers module knowledge checks, and advances through all modules — hands-free.

Built with **Playwright** (browser control) and **Claude Vision** (screen understanding).

---

## How It Works

The agent takes a screenshot every few seconds and asks Claude what it sees, then acts:

| What's on screen | What the agent does |
|---|---|
| Video playing | Sets playback to 16x speed and jumps near the end |
| Interactive slide (tabs, buttons, blocks) | Clicks each element, closes any popups |
| Module knowledge check / quiz | Reads the question, selects the correct answer, submits |
| Next / Continue / arrow button | Clicks it to advance |
| Module completion message | Finds the next unchecked module in the course menu and starts it |
| Login page | Stops and tells you to re-run the session saver |

---

## Compatibility

Works on any browser-based LMS or e-learning platform. If it runs in a browser, this agent can handle it.

Some platforms may need minor tweaks — see [Platform Notes](#platform-notes) below.

---

## Requirements

- macOS, Windows, or Linux
- Python 3.10+
- An [Anthropic API key](https://console.anthropic.com) with credits (~$5 covers most courses)

---

## Setup

### 1. Install Python dependencies

```bash
pip3 install playwright anthropic
python3 -m playwright install chromium
```

### 2. Set environment variables

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export TRAINING_URL="https://your-lms-platform.com"
```

To make these permanent on Mac/Linux, add them to `~/.zshrc`:

```bash
echo 'export ANTHROPIC_API_KEY="sk-ant-..."' >> ~/.zshrc
echo 'export TRAINING_URL="https://your-lms.com"' >> ~/.zshrc
source ~/.zshrc
```

On Windows (Command Prompt):
```cmd
set ANTHROPIC_API_KEY=sk-ant-...
set TRAINING_URL=https://your-lms.com
```

### 3. Save your browser session (one time only)

This opens a browser for you to log in manually. **Your credentials are never stored or sent anywhere.**

```bash
python3 save_session.py
```

- A browser window opens
- Log in to your training platform normally (including any SSO/MFA)
- Navigate into the course you want to complete
- Come back to the terminal and press **Enter**
- Your session cookies are saved to `browser-profile/`

### 4. Run the agent

```bash
python3 agent.py
```

Watch the browser window — the agent will work through the course automatically. You can minimize it and do other things.

---

## Configuration

All settings are at the top of `agent.py`:

```python
CHECK_INTERVAL_SEC  = 1     # How often the main loop polls
VIDEO_SKIP_SPEED    = 16    # Video playback multiplier (16 = 16x speed)
MAX_VIDEO_WAIT_SEC  = 600   # Max seconds to wait on a single video (10 min)
SCREENSHOT_QUALITY  = 90    # JPEG quality for screenshots (higher = more accurate)
POPUP_WAIT_SEC      = 20    # How long to wait for course popup window to appear
```

---

## Cost Estimate

Uses `claude-sonnet-4-6` by default (~$3/million input tokens).

| Course length | Estimated cost |
|---|---|
| 1 hour | ~$1.00 |
| 3 hours | ~$2.50 |
| 7 hours | ~$5–8 |

You can reduce cost further by increasing `CHECK_INTERVAL_SEC` (e.g. from 1 to 3).

---

## Session Expiry

If the agent hits a login page mid-run, your session expired. Just:

```bash
python3 save_session.py   # Log in again
python3 agent.py          # Resume
```

SSO sessions typically last several hours depending on your company's policy.

---

## Platform Notes

**Popup-based players** — Some platforms open course content in a separate popup window. The agent detects this automatically and switches focus to it.

**Inline players** — Platforms that load course content directly in the page work without any extra configuration.

**Platforms with timed slides** — If a slide has a mandatory wait timer, the agent will wait it out naturally since it polls every few seconds.

**Proctored assessments** — This agent is designed for training content (videos, interactive slides, module knowledge checks). It is not intended for use on proctored exams or graded assessments.

---

## Files

| File | Purpose |
|---|---|
| `agent.py` | Main agent — runs the course |
| `save_session.py` | One-time session saver — log in manually |
| `requirements.txt` | Python dependencies |
| `browser-profile/` | Your saved browser session (created by save_session.py) |
| `stuck_screenshot.jpg` | Saved when agent gets confused — check this to debug |

---

## Troubleshooting

**Agent gets stuck on a slide repeatedly**
Check `stuck_screenshot.jpg` to see what it's seeing. Usually means a new UI pattern the agent hasn't encountered. It will pause 30 seconds then retry automatically.

**"No browser profile found"**
Run `save_session.py` first.

**JSON parse errors in the logs**
Usually recovers automatically via the built-in salvage parser. If it persists, check your API key has sufficient credits at console.anthropic.com.

**Course popup doesn't open**
Set `TRAINING_URL` to the direct course player URL instead of the dashboard. Manually open a course, copy the URL from the popup window, and use that as your `TRAINING_URL`.

**Wrong module gets clicked after completion**
The agent looks for unchecked checkboxes in the course menu. If your platform uses a different completion indicator (color, icon, text), you may need to manually click to the next module once and let the agent take over from there.

**Agent clicks the wrong part of the screen**
Some platforms have different layouts. The agent uses Claude Vision to identify coordinates so it adapts automatically, but unusual layouts may need a run or two to stabilize.
