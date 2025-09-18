# streamlit_app.py
# Unibet Ctrl+A → Boss-shaped CSV/XLSX (Parse, Export) — Streamlit edition

import io
import re
from typing import List, Tuple, Dict, Optional

import pandas as pd
import streamlit as st

# ---------- Unibet parsing (robust dashes, no duplicates) ----------
RE_PLAYER_OF_MATCH = re.compile(r'^\s*Player\s+of\s+the\s+Match\s*$', re.IGNORECASE)
RE_TOP_BOWLER_TEAM = re.compile(r'^\s*Top\s+Bowler\s*[–—-]\s*(.*?)\s*[–—-]\s*1st\s*Innings\s*$', re.IGNORECASE)
RE_TOP_RUNSCORER_TEAM = re.compile(r'^\s*Top\s+Run\s*Scorer\s*[–—-]\s*(.*?)\s*[–—-]\s*1st\s*Innings\s*$', re.IGNORECASE)
RE_DECIMAL = re.compile(r'^\d+(?:\.\d+)?$')

# anything that should end a prices list (also stop on generic "Top Run Scorer"/"Top Bowler")
STOP_WORDS = {
    "view less","view more","odds format","help","safer gambling","about us","apps","blog",
    "fair gaming policy","unibet community","all sports","home","in-play","explore","favourites",
    "search sports, leagues or teams","toss winner","winner (incl. super over)","match","over","dismissal",
    "sports","casino","live casino","bingo","poker","top run scorer","top bowler"
}

def _lines(text: str) -> List[str]:
    return [ln.strip() for ln in text.replace("\r\n","\n").replace("\r","\n").split("\n") if ln.strip()]

def _is_heading(line: str) -> bool:
    return (
        RE_PLAYER_OF_MATCH.match(line) is not None or
        RE_TOP_BOWLER_TEAM.match(line) is not None or
        RE_TOP_RUNSCORER_TEAM.match(line) is not None
    )

def _is_boundary(line: str) -> bool:
    low = line.lower()
    return _is_heading(line) or (low in STOP_WORDS)

def _parse_block(lines: List[str], start: int, market: str, team: Optional[str]) -> Tuple[List[Dict], int]:
    rows, j, pending = [], start + 1, None
    while j < len(lines):
        s = lines[j].strip()
        if _is_boundary(s): break
        if RE_DECIMAL.match(s):
            if pending:
                rows.append({"Market": market, "Team": team or "", "SelectionName": pending, "SelectionOdds": s})
                pending = None
        else:
            pending = s
        j += 1
    return rows, j

def parse_unibet(text: str) -> pd.DataFrame:
    lines = _lines(text or "")
    out, i = [], 0
    while i < len(lines):
        s = lines[i]
        if RE_PLAYER_OF_MATCH.match(s):
            r, i = _parse_block(lines, i, "Player of the Match", None); out += r; continue
        m = RE_TOP_BOWLER_TEAM.match(s)
        if m:
            team = m.group(1).strip()
            r, i = _parse_block(lines, i, "Top Bowler", team); out += r; continue
        m = RE_TOP_RUNSCORER_TEAM.match(s)
        if m:
            team = m.group(1).strip()
            # rename to Top Batter
            r, i = _parse_block(lines, i, "Top Batter", team); out += r; continue
        i += 1
    return pd.DataFrame(out, columns=["Market","Team","SelectionName","SelectionOdds"]).reset_index(drop=True)

def detect_teams(parsed: pd.DataFrame) -> List[str]:
    order = []
    for _, r in parsed[parsed.Market.isin(["Top Bowler","Top Batter"])].iterrows():
        t = r["Team"]
        if t and t not in order: order.append(t)
    if len(order) < 2:
        uniq = sorted([t for t in parsed.Team.unique() if t])
        order = (uniq + ["—","—"])[:2]
    return order[:2]

# ---------- Boss helpers ----------
def _norm_key(s: str) -> str:
    return re.sub(r'[^a-z0-9]', '', s.lower())

def find_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    norm = {_norm_key(c): c for c in df.columns}
    for cand in candidates:
        key = _norm_key(cand)
        if key in norm: return norm[key]
    return None

def unique_markets(df: pd.DataFrame) -> pd.DataFrame:
    subset_keys = {"marketid","marketname","markettypeid","markettypename","startdate","suspenddate","startsuspensiondate"}
    keep = [c for c in df.columns if _norm_key(c) in subset_keys]
    return df[keep].drop_duplicates().reset_index(drop=True) if keep else pd.DataFrame()

def build_template_map(boss: pd.DataFrame, market_name_col: str) -> Dict[str, pd.Series]:
    def _norm_space(s: str) -> str:
        return re.sub(r"\s+"," ", str(s or "")).strip().lower()
    m = {}
    for _, row in boss.iterrows():
        key = _norm_space(row.get(market_name_col, ""))
        if key and key not in m: m[key] = row
    return m

def replicate_from_template(template: pd.Series,
                            selections: pd.DataFrame,
                            outcols: List[str],
                            sel_name_col: str,
                            sel_odds_col: str) -> pd.DataFrame:
    base = pd.DataFrame([template.to_dict()] * len(selections))
    base[sel_name_col] = selections["SelectionName"].values
    base[sel_odds_col] = selections["SelectionOdds"].values
    # If these exist, blank them; do not create new ones
    for cset in (["FirstOdds","firstodds","Firstodds"],
                 ["LastOdds","lastodds","Lastodds"],
                 ["AnyOdds","anyodds","Anyodds"]):
        col = find_col(base, cset)
        if col: base[col] = ""
    return base[outcols]

# ---------- Streamlit UI ----------
st.set_page_config(page_title="Unibet → Boss Export", page_icon="📤", layout="wide")
st.title("Unibet → Boss Export")
st.caption("Paste Unibet page text; export Boss-shaped CSV/XLSX. No MarketID typing. Keeps your Boss columns/order.")

# Inputs
boss_file = st.file_uploader("1) Upload Boss Player Props CSV/XLSX export", type=["csv","xlsx"])
unibet_text = st.text_area("2) Paste Unibet text (Ctrl+A from page)", height=260, placeholder="Player of the Match …\nTop Bowler – Team – 1st Innings …\nTop Run Scorer – Team – 1st Innings …")

colA, colB = st.columns([1,1])
parse_click = colA.button("Parse", type="primary")
export_click = colB.button("Export (CSV+XLSX)")

# Session state
if "STATE" not in st.session_state:
    st.session_state.STATE = {
        "boss": None, "parsed": None, "tmap": None, "outcols": None,
        "sel_name_col": None, "sel_odds_col": None, "market_name_col": None
    }

STATE = st.session_state.STATE

def read_boss_from_upload(up_file) -> pd.DataFrame:
    if up_file is None:
        return pd.DataFrame()
    name = (up_file.name or "").lower()
    data = up_file.read()
    bio = io.BytesIO(data)
    if name.endswith(".xlsx"):
        df = pd.read_excel(bio, dtype=str).fillna("")
    else:
        df = pd.read_csv(bio, dtype=str).fillna("")
    # Preserve exact columns/order as uploaded
    return df[df.columns]

# ---------- Parse ----------
if parse_click:
    if boss_file is None:
        st.error("Upload a Boss export first.")
    else:
        boss = read_boss_from_upload(boss_file)
        if boss.empty:
            st.error("Uploaded Boss file is empty or unsupported.")
        else:
            market_name_col = find_col(boss, ["MarketName","marketname"])
            sel_name_col = find_col(boss, ["SelectionName","selectionname","Selection","Runner","Name"])
            sel_odds_col = find_col(boss, ["SelectionOdds","selectionodds","Odds","Price","DecimalOdds"])
            if not market_name_col or not sel_name_col or not sel_odds_col:
                st.error("Boss file must contain columns for MarketName, SelectionName and SelectionOdds.")
            else:
                parsed = parse_unibet(unibet_text or "")
                if parsed.empty:
                    st.error("No selections parsed from Unibet text.")
                else:
                    counts = parsed.groupby(["Market","Team"]).size().reset_index(name="rows")
                    st.subheader("Parsed counts")
                    st.dataframe(counts, use_container_width=True)
                    st.subheader("Boss template markets (sample)")
                    st.dataframe(unique_markets(boss).head(200), use_container_width=True)

                    STATE.update({
                        "boss": boss,
                        "parsed": parsed,
                        "tmap": build_template_map(boss, market_name_col),
                        "outcols": list(boss.columns),
                        "sel_name_col": sel_name_col,
                        "sel_odds_col": sel_odds_col,
                        "market_name_col": market_name_col
                    })
                    st.success(f"Parsed. Using columns: {sel_name_col} / {sel_odds_col}. Ready to Export.")

# ---------- Export ----------
if export_click:
    boss = STATE.get("boss"); parsed = STATE.get("parsed")
    if boss is None or parsed is None:
        st.error("Click Parse first.")
    else:
        outcols = STATE["outcols"]; tmap = STATE["tmap"]
        sel_name_col = STATE["sel_name_col"]; sel_odds_col = STATE["sel_odds_col"]

        def tpl_by_name(name: str) -> Optional[pd.Series]:
            key = re.sub(r"\s+"," ", name.strip().lower())
            return tmap.get(key, None)

        chunks, notes = [], []

        # 1) Player of the Match
        potm_sel = parsed[parsed.Market=="Player of the Match"]
        potm_tpl = tpl_by_name("Player of the Match")
        if potm_tpl is not None and not potm_sel.empty:
            chunks.append(replicate_from_template(potm_tpl, potm_sel, outcols, sel_name_col, sel_odds_col))
        else:
            notes.append("Player of the Match: missing template row or no selections.")

        # 2) Teams (Top Bowler + Top Batter)
        teams = detect_teams(parsed)
        for team in teams:
            if not team: continue

            # Top Bowler
            tb_sel = parsed[(parsed.Market=="Top Bowler") & (parsed.Team==team)]
            tb_tpl = tpl_by_name(f"{team} Top Bowler")
            if tb_tpl is None:
                tb_tpl = tpl_by_name(f"Top Bowler - {team} - 1st Innings")

            tbat_tpl = tpl_by_name(f"{team} Top Batter")
                if tbat_tpl is None:
            tbat_tpl = tpl_by_name(f"Top Batter - {team} - 1st Innings")

            if tb_tpl is not None and not tb_sel.empty:
                chunks.append(replicate_from_template(tb_tpl, tb_sel, outcols, sel_name_col, sel_odds_col))
            else:
                notes.append(f"Top Bowler — {team}: no template row or no selections.")

            # Top Batter
            tbat_sel = parsed[(parsed.Market=="Top Batter") & (parsed.Team==team)]
            tbat_tpl = tpl_by_name(f"{team} Top Batter") or tpl_by_name(f"Top Batter - {team} - 1st Innings")
            if tbat_tpl is not None and not tbat_sel.empty:
                chunks.append(replicate_from_template(tbat_tpl, tbat_sel, outcols, sel_name_col, sel_odds_col))
            else:
                notes.append(f"Top Batter — {team}: no template row or no selections.")

        if not chunks:
            st.error("No output built." + (" " + "; ".join(notes) if notes else ""))
        else:
            out_df = pd.concat(chunks, ignore_index=True)

            # Build CSV (UTF-8 BOM) and XLSX in-memory for Streamlit downloads
            csv_bytes = out_df.to_csv(index=False).encode("utf-8-sig")

            xlsx_buffer = io.BytesIO()
            with pd.ExcelWriter(xlsx_buffer, engine="openpyxl") as w:
                out_df.to_excel(w, index=False, sheet_name="upload")
            xlsx_buffer.seek(0)

            st.success("Export built.")
            st.dataframe(out_df.head(50), use_container_width=True)

            col1, col2 = st.columns(2)
            col1.download_button(
                "⬇️ Download CSV (UTF-8 BOM)",
                data=csv_bytes,
                file_name="boss_upload_ready.csv",
                mime="text/csv"
            )
            col2.download_button(
                "⬇️ Download XLSX",
                data=xlsx_buffer,
                file_name="boss_upload_ready.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

            if notes:
                st.info("Notes: " + "; ".join(notes))
