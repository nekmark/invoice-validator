"""Look and feel: burgundy, frosted glass, a little sparkle.

Everything here is local CSS + emoji — no web fonts, images or scripts are
loaded from the internet, so opening the app never contacts another server.
"""

from html import escape

# Status colours stay clearly distinct from each other and from the burgundy
# theme, and every status always shows an icon + its label, so meaning
# never depends on colour alone.
STATUS_STYLE = {
    "Pass": {"icon": "♥️", "bg": "#dcfce7", "fg": "#166534", "border": "#86efac"},
    "Warning": {"icon": "🚩", "bg": "#ffe1e1", "fg": "#b91c1c", "border": "#fca5a5"},
    "Manual Review": {"icon": "👀", "bg": "#fff1d6", "fg": "#92400e", "border": "#fcd34d"},
    "Unable to Verify": {"icon": "☁️", "bg": "#ede9fe", "fg": "#5b4b8a", "border": "#c4b5fd"},
}

CSS = """
<style>
:root {
  --wine-50: #faf7f5;
  --wine-100: #f3ebe9;
  --wine-200: #e8d9d9;
  --wine-300: #a63d5a;
  --wine-400: #8f2f4c;
  --wine-500: #7a1f3d;
  --wine-600: #5c1230;
  --wine-800: #4a0f27;
  --plum: #2b1a1e;
  --muted: #7a5c63;
  --glass: rgba(255, 255, 255, 0.52);
  --glass-strong: rgba(255, 255, 255, 0.72);
  --glass-border: rgba(255, 255, 255, 0.78);
  --glow: 0 10px 40px rgba(92, 18, 48, 0.10), inset 0 1px 0 rgba(255, 255, 255, 0.85);
  /* Built-in macOS faces only (no web fonts in a confidential tool): an
     editorial serif for titles and numbers, a clean sans for everything else. */
  --font-display: "Optima", "Avenir Next", "Gill Sans", system-ui, sans-serif;
  --font: "Optima", "Avenir Next", -apple-system, "Helvetica Neue", system-ui, sans-serif;
}

/* ---------- canvas ---------- */
.stApp {
  font-family: var(--font);
  color: var(--plum);
  background:
    radial-gradient(circle at 8% 12%, #efe4e4 0, transparent 38%),
    radial-gradient(circle at 92% 8%, #ece8ea 0, transparent 34%),
    radial-gradient(circle at 85% 88%, #f0e6e6 0, transparent 40%),
    radial-gradient(circle at 12% 92%, #f4ecec 0, transparent 36%),
    linear-gradient(135deg, #faf7f5 0%, #f5efee 48%, #f7f3f2 100%);
  background-attachment: fixed;
}
.stApp::before, .stApp::after {
  content: "";
  position: fixed;
  width: 420px;
  height: 420px;
  border-radius: 50%;
  filter: blur(70px);
  opacity: 0.35;
  pointer-events: none;
  z-index: 0;
  animation: float 22s ease-in-out infinite alternate;
}
.stApp::before { background: #d9b8be; top: -120px; left: 35%; }
.stApp::after { background: #cdbfc9; bottom: -140px; right: 10%; animation-delay: -11s; }
@keyframes float {
  from { transform: translate(0, 0) scale(1); }
  to { transform: translate(60px, 40px) scale(1.12); }
}
@media (prefers-reduced-motion: reduce) {
  .stApp::before, .stApp::after { animation: none; }
}
.block-container, [data-testid="stMainBlockContainer"] {
  position: relative;
  z-index: 1;
  padding-top: 2.2rem;
  max-width: 1500px;
}
[data-testid="stHeader"] { background: transparent; }
::selection { background: var(--wine-200); color: var(--plum); }

.stApp p, .stApp label, .stApp li, .stApp input, .stApp textarea, .stApp button,
.stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5 { font-family: var(--font-display); }
.stApp p, .stApp li, .stApp label { line-height: 1.55; }
[data-testid="stIconMaterial"] { font-family: "Material Symbols Rounded" !important; }

/* ---------- type ---------- */
.stApp h1 {
  font-weight: 400;
  font-size: 2.7rem;
  letter-spacing: 0;
  color: var(--wine-600);
  padding-bottom: 0.1rem;
}
.stApp h1::after {
  content: "";
  display: block;
  width: 52px;
  height: 3px;
  margin-top: 0.45rem;
  border-radius: 3px;
  background: linear-gradient(90deg, var(--wine-500), var(--wine-300));
}
.stApp h2, .stApp h3 { color: #4a0f27; font-weight: 400; letter-spacing: 0.005em; }
.stApp h3 { font-size: 1.7rem; }
.stApp h4, .stApp h5 { color: var(--wine-800); font-weight: 400; letter-spacing: 0.005em; }
.stApp h4 { font-size: 1.3rem; margin-top: 0.6rem; }
[data-testid="stCaptionContainer"], .stApp small { color: var(--muted) !important; }
.stApp code {
  background: rgba(255, 255, 255, 0.7);
  color: var(--wine-600);
  border-radius: 8px;
  padding: 0.1em 0.4em;
}

/* ---------- glass surfaces ---------- */
[data-baseweb="tab-panel"],
[data-testid="stForm"],
[data-testid="stExpander"] details,
[data-testid="stJson"],
[data-testid="stTable"],
.glass {
  background: var(--glass);
  -webkit-backdrop-filter: blur(18px) saturate(160%);
  backdrop-filter: blur(18px) saturate(160%);
  border: 1px solid var(--glass-border) !important;
  border-radius: 24px !important;
  box-shadow: var(--glow);
}
[data-baseweb="tab-panel"] { padding: 1.6rem 1.8rem 1.8rem; margin-top: 0.9rem; }
[data-testid="stForm"] { padding: 1.3rem 1.4rem; }
[data-testid="stJson"] { padding: 0.8rem 1rem; }
[data-testid="stExpander"] details summary { border-radius: 24px; }
[data-testid="stExpander"] details summary:hover { color: var(--wine-600); }
[data-testid="stDataFrame"] {
  border-radius: 20px;
  overflow: hidden;
  border: 1px solid var(--glass-border);
  box-shadow: var(--glow);
}
[data-testid="stAlert"] > div {
  border-radius: 18px;
  -webkit-backdrop-filter: blur(12px);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(255, 255, 255, 0.7);
}

/* ---------- tabs as pills ---------- */
[data-baseweb="tab-list"] {
  gap: 0.35rem;
  width: fit-content;
  padding: 0.35rem;
  background: var(--glass);
  -webkit-backdrop-filter: blur(16px);
  backdrop-filter: blur(16px);
  border: 1px solid var(--glass-border);
  border-radius: 999px;
  box-shadow: var(--glow);
}
[data-baseweb="tab-highlight"], [data-baseweb="tab-border"] { display: none; }
button[data-baseweb="tab"] {
  height: auto;
  padding: 0.5rem 1.25rem;
  border-radius: 999px;
  background: transparent;
  transition: background 0.2s ease, transform 0.2s ease;
}
button[data-baseweb="tab"] p { font-weight: 600; font-size: 0.92rem; letter-spacing: 0.01em; color: var(--wine-800); }
button[data-baseweb="tab"]:hover { background: rgba(255, 255, 255, 0.65); }
button[data-baseweb="tab"][aria-selected="true"] {
  background: linear-gradient(135deg, #a63d5a 0%, #7a1f3d 60%, #5c1230 100%);
  box-shadow: 0 6px 18px rgba(92, 18, 48, 0.28);
}
button[data-baseweb="tab"][aria-selected="true"] p { color: #fff; }

/* ---------- tabs as pills (Streamlit 1.5x+ markup) ---------- */
[data-testid="stTabs"] [role="tablist"] {
  gap: 0.35rem;
  width: fit-content;
  height: auto;
  padding: 0.35rem;
  background: var(--glass);
  -webkit-backdrop-filter: blur(16px);
  backdrop-filter: blur(16px);
  border: 1px solid var(--glass-border);
  border-radius: 999px;
  box-shadow: var(--glow);
}
[data-testid="stTabs"] [role="tablist"]::after { display: none; }
[data-testid="stTab"] {
  height: auto;
  padding: 0.5rem 1.25rem;
  border-radius: 999px;
  transition: background 0.2s ease;
}
[data-testid="stTab"] > div:not([data-testid]) { display: none; }  /* the underline indicator */
[data-testid="stTab"] p { font-weight: 600; font-size: 0.92rem; letter-spacing: 0.01em; color: var(--wine-800); }
[data-testid="stTab"]:hover { background: rgba(255, 255, 255, 0.65); }
[data-testid="stTab"][aria-selected="true"] {
  background: linear-gradient(135deg, #a63d5a 0%, #7a1f3d 60%, #5c1230 100%);
  box-shadow: 0 6px 18px rgba(92, 18, 48, 0.28);
}
[data-testid="stTab"][aria-selected="true"] p { color: #fff; }
[data-testid="stTabPanel"] {
  padding: 1.6rem 1.8rem 1.8rem;
  margin-top: 0.9rem;
  background: var(--glass);
  -webkit-backdrop-filter: blur(18px) saturate(160%);
  backdrop-filter: blur(18px) saturate(160%);
  border: 1px solid var(--glass-border);
  border-radius: 24px;
  box-shadow: var(--glow);
}

/* ---------- buttons ---------- */
.stButton > button, .stFormSubmitButton > button, .stDownloadButton > button {
  border-radius: 999px;
  padding: 0.5rem 1.35rem;
  background: var(--glass-strong);
  -webkit-backdrop-filter: blur(10px);
  backdrop-filter: blur(10px);
  border: 1px solid rgba(92, 18, 48, 0.24);
  color: var(--wine-800);
  font-weight: 600;
  box-shadow: 0 4px 14px rgba(92, 18, 48, 0.1);
  transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
}
.stButton > button:hover, .stFormSubmitButton > button:hover, .stDownloadButton > button:hover {
  transform: translateY(-1px);
  border-color: var(--wine-500);
  color: var(--wine-600);
  box-shadow: 0 8px 22px rgba(92, 18, 48, 0.18);
}
.stButton > button:focus-visible, .stFormSubmitButton > button:focus-visible {
  outline: 3px solid var(--wine-300);
  outline-offset: 2px;
}
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"],
[data-testid="stBaseButton-primary"], [data-testid="baseButton-primary"],
[data-testid="stBaseButton-primaryFormSubmit"], [data-testid="baseButton-primaryFormSubmit"],
.stApp button[kind^="primary"] {
  background: linear-gradient(135deg, #a63d5a 0%, #7a1f3d 55%, #5c1230 100%);
  border: none;
  color: #fff;
  box-shadow: 0 8px 24px rgba(92, 18, 48, 0.3);
}
.stButton > button[kind="primary"] p, [data-testid="stBaseButton-primary"] p,
[data-testid="baseButton-primary"] p, .stApp button[kind^="primary"] p { color: #fff; }
.stApp button[kind^="primary"]:hover { color: #fff; border: none; }
.stButton > button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover {
  color: #fff;
  box-shadow: 0 10px 30px rgba(92, 18, 48, 0.4);
}
.stButton > button:disabled { opacity: 0.45; transform: none; box-shadow: none; }

/* ---------- inputs ---------- */
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="select"] > div, [data-baseweb="textarea"] {
  border-radius: 14px !important;
  background: rgba(255, 255, 255, 0.75) !important;
  border-color: rgba(92, 18, 48, 0.2) !important;
}
[data-baseweb="input"] input, [data-baseweb="base-input"] input { background: transparent !important; }
/* inputs (Streamlit 1.5x+ markup) */
[data-testid="stTextInputRootElement"], [data-testid="stSelectbox"] [role="group"],
[data-testid="stNumberInputContainer"], [data-testid="stTextAreaRootElement"] {
  border-radius: 14px !important;
  background: rgba(255, 255, 255, 0.75) !important;
  border: 1px solid rgba(92, 18, 48, 0.2) !important;
}
[data-testid="stTextInputRootElement"]:focus-within, [data-testid="stSelectbox"] [role="group"]:focus-within,
[data-testid="stNumberInputContainer"]:focus-within, [data-testid="stTextAreaRootElement"]:focus-within {
  border-color: var(--wine-500) !important;
  box-shadow: 0 0 0 3px rgba(166, 61, 90, 0.25);
}
[data-testid="stTextInputField"], [data-testid="stNumberInputField"],
[data-testid="stSelectbox"] [role="group"] input { background: transparent !important; }
[data-baseweb="input"]:focus-within, [data-baseweb="select"] > div:focus-within {
  border-color: var(--wine-500) !important;
  box-shadow: 0 0 0 3px rgba(166, 61, 90, 0.25);
}
[data-testid="stNumberInput"] button { border-radius: 10px; }
[data-testid="stCheckbox"] span[role="checkbox"][aria-checked="true"],
[data-baseweb="checkbox"] [aria-checked="true"] { background: var(--wine-500) !important; }

/* ---------- progress ---------- */
[data-testid="stProgress"] [role="progressbar"] > div > div {
  background-image: linear-gradient(90deg, #a63d5a, #7a1f3d, #7a1f3d) !important;
  border-radius: 999px;
}

/* ---------- custom pieces ---------- */
.hero-badge, .chip {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.28rem 0.8rem;
  border-radius: 999px;
  font-size: 0.78rem;
  font-weight: 600;
  letter-spacing: 0.03em;
  background: var(--glass-strong);
  border: 1px solid var(--glass-border);
  color: var(--wine-800);
  box-shadow: 0 4px 14px rgba(92, 18, 48, 0.1);
}
.hero-badge { margin-bottom: 0.2rem; }
/* Header, right column (st.container(horizontal_alignment="right") does the
   alignment): Log out level with the title, Privacy switch under it. */
.st-key-header_right .stButton > button { margin: 0; }
.hero-sub {
  color: var(--muted);
  margin: 0.1rem 0 1.1rem;
  font-family: var(--font-display);
  font-style: italic;
  font-size: 1.25rem;
  letter-spacing: 0.01em;
}
.chips { display: flex; flex-wrap: wrap; gap: 0.45rem; margin: 0.3rem 0 0.8rem; }
.chip { font-weight: 600; }

.tiles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 0.9rem;
  margin: 0.4rem 0 1.2rem;
}
.tile {
  padding: 1rem 1.1rem;
  border-radius: 22px;
  background: var(--glass-strong);
  -webkit-backdrop-filter: blur(16px);
  backdrop-filter: blur(16px);
  border: 1px solid var(--glass-border);
  box-shadow: var(--glow);
  transition: transform 0.2s ease;
}
/* Plain tiles are information only: no hover movement, so they don't pretend
   to be buttons. The Dashboard's status tiles ARE buttons (stat_buttons below). */
.tile:hover { transform: none; }
[class*="st-key-tile_"] .stButton > button {
  width: 100%;
  min-height: 112px;
  padding: 1rem 1.2rem;
  border-radius: 22px;
  border: 1px solid var(--glass-border);
  background: var(--glass-strong);
  -webkit-backdrop-filter: blur(16px);
  backdrop-filter: blur(16px);
  box-shadow: var(--glow);
  color: var(--plum);
  font-family: var(--font);
  font-size: 1.08rem;
  font-weight: 600;
  letter-spacing: 0.02em;
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}
[class*="st-key-tile_"] .stButton > button p { font-size: 1.08rem; line-height: 1.35; }
[class*="st-key-tile_"] .stButton > button p strong { font-family: var(--font-display); font-size: 2.1rem; font-weight: 400; display: block; margin-top: 0.3rem; }
[class*="st-key-tile_"] .stButton > button:hover {
  transform: scale(1.05) translateY(-3px);
  box-shadow: 0 16px 44px rgba(92, 18, 48, 0.22), inset 0 1px 0 rgba(255, 255, 255, 0.85);
  cursor: pointer;
}
[class*="st-key-tile_"] .stButton > button:active { transform: scale(0.99); }
/* The tile that is the active filter: burgundy with white text (status tints apply only to the others). */
[class*="st-key-tile_"] .stButton > button[kind="primary"],
[class*="st-key-tile_"] .stButton > button[kind="primary"]:hover {
  background: linear-gradient(135deg, var(--wine-300) 0%, var(--wine-500) 55%, var(--wine-600) 100%) !important;
  border-color: transparent !important;
  color: #fff !important;
}
[class*="st-key-tile_"] .stButton > button[kind="primary"] p { color: #fff !important; }
.tile-top { display: flex; justify-content: space-between; align-items: center; }
.tile-emoji { font-size: 1.35rem; }
.tile-num {
  font-family: var(--font-display);
  font-size: 2.2rem;
  font-weight: 400;
  line-height: 1.1;
  margin-top: 0.35rem;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.01em;
}
.tile-label {
  font-size: 0.74rem;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  opacity: 0.75;
}

.stApp ::-webkit-scrollbar { width: 10px; height: 10px; }
.stApp ::-webkit-scrollbar-thumb { background: var(--wine-200); border-radius: 999px; }
.stApp ::-webkit-scrollbar-thumb:hover { background: var(--wine-300); }
</style>
"""


def apply_theme(st):
    st.markdown(CSS, unsafe_allow_html=True)


def hero(st, badge: str):
    """Small badge above the page title (st.title is rendered by the caller
    so it stays a real heading)."""
    st.markdown(f'<span class="hero-badge">{escape(badge)}</span>', unsafe_allow_html=True)


def tagline(st, text: str):
    st.markdown(f'<p class="hero-sub">{escape(text)}</p>', unsafe_allow_html=True)


def status_chips(st):
    chips = "".join(
        f'<span class="chip" style="background:{s["bg"]};color:{s["fg"]};border-color:{s["border"]}">'
        f'{s["icon"]} {escape(label)}</span>'
        for label, s in STATUS_STYLE.items()
    )
    st.markdown(f'<div class="chips">{chips}</div>', unsafe_allow_html=True)


def kpi_tiles(st, items):
    """Neutral burgundy tiles for any numbers: items = [(label, emoji, value)]."""
    tiles = "".join(
        f'<div class="tile"><div class="tile-top"><span class="tile-label">{escape(str(label))}</span>'
        f'<span class="tile-emoji">{emoji}</span></div><div class="tile-num">{escape(str(value))}</div></div>'
        for label, emoji, value in items
    )
    st.markdown(f'<div class="tiles">{tiles}</div>', unsafe_allow_html=True)


def _slug(label: str) -> str:
    return label.lower().replace(" ", "_")


def status_tile_css() -> str:
    """Per-status tint for the Dashboard's tile buttons (keyed containers)."""
    rules = []
    for label, s in STATUS_STYLE.items():
        # Only the tiles that are NOT the active filter get the tint; the active one
        # keeps the burgundy "primary" look so it's obvious which filter is on.
        sel = f'.stApp .st-key-tile_{_slug(label)} .stButton > button:not([kind="primary"])'
        rules.append(
            f'{sel}, {sel}:hover {{ background: linear-gradient(160deg, rgba(255,255,255,.75), {s["bg"]}) !important; '
            f'color: {s["fg"]} !important; border-color: {s["border"]} !important; }}'
            f' {sel} p {{ color: {s["fg"]} !important; }}'
        )
    return "<style>" + "".join(rules) + "</style>"


def stat_buttons(st, total: int, counts: dict, selected: str = "All"):
    """The Dashboard's stat tiles as real buttons: click one to filter the
    table by that status (the 'All invoices' tile clears the filter).
    Returns the label that was clicked this run, or None."""
    st.markdown(status_tile_css(), unsafe_allow_html=True)
    clicked = None
    items = [("All", "All invoices", "🧾", total)] + [
        (label, label, s["icon"], counts.get(label, 0)) for label, s in STATUS_STYLE.items()
    ]
    cols = st.columns(len(items))
    for col, (value, label, icon, n) in zip(cols, items):
        with col.container(key=f"tile_{_slug(label)}"):
            if st.button(f"{icon} {label}  \n**{n}**", key=f"tilebtn_{_slug(label)}", width="stretch",
                         type="primary" if selected == value else "secondary"):
                clicked = value
    return clicked


def stat_tiles(st, total: int, counts: dict):
    tiles = [
        '<div class="tile"><div class="tile-top"><span class="tile-label">All invoices</span>'
        f'<span class="tile-emoji">🧾</span></div><div class="tile-num">{total}</div></div>'
    ]
    for label, s in STATUS_STYLE.items():
        tiles.append(
            f'<div class="tile" style="background:linear-gradient(160deg, rgba(255,255,255,.75), {s["bg"]});'
            f'color:{s["fg"]};border-color:{s["border"]}">'
            f'<div class="tile-top"><span class="tile-label">{escape(label)}</span>'
            f'<span class="tile-emoji">{s["icon"]}</span></div>'
            f'<div class="tile-num">{counts.get(label, 0)}</div></div>'
        )
    st.markdown(f'<div class="tiles">{"".join(tiles)}</div>', unsafe_allow_html=True)
