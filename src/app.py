"""
app.py — ClaimGuard Fraud Detection Platform
Clean, professional claims management dashboard.
HOW TO RUN:
    streamlit run src/app.py
"""
import json, os, sys, tempfile
from pathlib import Path
from collections import Counter
import streamlit as st

SRC_DIR  = Path(__file__).resolve().parent
BASE_DIR = SRC_DIR.parent
sys.path.insert(0, str(SRC_DIR))

try:
    from rule_checks import RuleEngine
    RULES_AVAILABLE = True
except ImportError:
    RULES_AVAILABLE = False

try:
    from llm_checker import LLMChecker
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False

try:
    from parse_edi import parse_edi_file
    EDI_AVAILABLE = True
except ImportError:
    EDI_AVAILABLE = False

try:
    from extract_fields import process_pdf
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

CLAIMS_JSON = BASE_DIR / "data" / "raw_claims" / "claims.json"
NCCI_PATH   = BASE_DIR / "data" / "ncci_edits.xlsx"

st.set_page_config(page_title="ClaimGuard", page_icon="🛡️",
                   layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&family=DM+Mono:wght@400;500&display=swap');

html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
.stApp { background: #F2F4F7; }
.block-container { padding: 0 !important; max-width: 100% !important; }

section[data-testid="stSidebar"] { background: #1B2B45 !important; min-width:220px !important; max-width:220px !important; }
section[data-testid="stSidebar"] > div { background:#1B2B45 !important; padding:0 !important; }
[data-testid="collapsedControl"] { display:none !important; }
#MainMenu, footer, header { visibility:hidden; }

/* ── ALL VISIBLE ACTION BUTTONS (default) ─────────────────────────── */
/* Top-level rule: all main-area buttons are visible navy by default */
section[data-testid="stMain"] .stButton > button {
    background: #1B2B45 !important;
    color: white !important;
    border: none !important;
    border-radius: 6px !important;
    font-family: 'DM Sans', sans-serif !important;
    font-size: 13px !important;
    font-weight: 600 !important;
    padding: 9px 20px !important;
    opacity: 1 !important;
    height: auto !important;
    min-height: 38px !important;
    position: static !important;
    margin-top: 0 !important;
    z-index: auto !important;
    cursor: pointer !important;
}
section[data-testid="stMain"] .stButton > button:hover {
    background: #243650 !important;
}

/* ── RUN FRAUD ANALYSIS buttons — blue accent ─────────────────────── */
[data-testid="stButton-go_edi"] > button,
[data-testid="stButton-go_pdf"] > button,
[data-testid="stButton-go_sample"] > button {
    background: #2563EB !important;
    color: white !important;
    font-size: 14px !important;
    padding: 10px 24px !important;
    border-radius: 7px !important;
}
[data-testid="stButton-go_edi"] > button:hover,
[data-testid="stButton-go_pdf"] > button:hover,
[data-testid="stButton-go_sample"] > button:hover {
    background: #1D4ED8 !important;
}

/* ── BACK/HOME buttons — minimal ghost style ──────────────────────── */
[data-testid="stButton-submit_home"] > button,
[data-testid="stButton-perf_home"] > button {
    background: transparent !important;
    color: #6B7280 !important;
    border: 1px solid #E5E7EB !important;
    font-size: 12px !important;
    font-weight: 400 !important;
    padding: 6px 14px !important;
}
[data-testid="stButton-submit_home"] > button:hover,
[data-testid="stButton-perf_home"] > button:hover {
    background: #F9FAFB !important;
    color: #374151 !important;
}

/* ── ROW OVERLAY BUTTONS — invisible click targets only ───────────── */
/* Only buttons whose text starts with "CLM" (claim IDs) get hidden */
[data-testid^="stButton-row_CLM"] > button {
    background: transparent !important;
    color: transparent !important;
    border: none !important;
    box-shadow: none !important;
    margin-top: -48px !important;
    height: 48px !important;
    min-height: 48px !important;
    width: 100% !important;
    position: relative !important;
    z-index: 10 !important;
    opacity: 0.01 !important;
    font-size: 0 !important;
    padding: 0 !important;
}

/* ── SIDEBAR BUTTONS ──────────────────────────────────────────────── */
section[data-testid="stSidebar"] .stButton > button {
    background: transparent !important;
    color: #9CA3AF !important;
    text-align: left !important;
    font-size: 12px !important;
    font-weight: 400 !important;
    padding: 7px 16px !important;
    border: none !important;
    border-radius: 4px !important;
    justify-content: flex-start !important;
    opacity: 1 !important;
    height: auto !important;
    min-height: unset !important;
    margin-top: 0 !important;
    position: static !important;
}
section[data-testid="stSidebar"] .stButton > button:hover {
    background: #243650 !important;
    color: white !important;
}

/* ── TABS ─────────────────────────────────────────────────────────── */
.stTabs [data-baseweb="tab-list"] { background:white; border-bottom:1px solid #E5E7EB; padding:0 24px; gap:0; }
.stTabs [data-baseweb="tab"] {
    font-family:'DM Sans',sans-serif !important; font-size:13px !important;
    font-weight:500 !important; color:#9CA3AF !important;
    padding:12px 16px !important; border-bottom:2px solid transparent !important;
    margin-bottom:-1px !important;
}
.stTabs [aria-selected="true"] { color:#1B2B45 !important; border-bottom-color:#1B2B45 !important; }
.stTabs [data-baseweb="tab-panel"] { padding:0 !important; }

/* ── FORM ELEMENTS ────────────────────────────────────────────────── */
.stTextInput input {
    border:1px solid #E5E7EB !important; border-radius:6px !important;
    font-size:13px !important; background:white !important; padding:9px 12px !important;
}
div[data-testid="stSelectbox"] > div > div { border-color:#E5E7EB !important; border-radius:6px !important; font-size:13px !important; }
[data-testid="stExpander"] { border:1px solid #E5E7EB !important; border-radius:8px !important; background:white !important; }
div[data-testid="stFileUploader"] > div { border:1.5px dashed #D1D5DB !important; border-radius:8px !important; background:white !important; }

/* ── METRIC CARDS ROW ─────────────────────────────────────────────── */
.cg-metric-row {
    display:grid; grid-template-columns:repeat(4,1fr); gap:0;
    background:white; border-bottom:1px solid #E5E7EB;
}
.cg-metric-card { padding:14px 24px; border-right:1px solid #E5E7EB; }
.cg-metric-card:last-child { border-right:none; }
.cg-metric-label { font-size:10px; font-weight:600; color:#9CA3AF; letter-spacing:0.08em; text-transform:uppercase; margin-bottom:4px; }
.cg-metric-value { font-size:24px; font-weight:300; font-family:'DM Mono',monospace; line-height:1; color:#1B2B45; }
.cg-metric-value.red { color:#DC2626; }
.cg-metric-value.teal { color:#0F766E; }
.cg-metric-chip { display:inline-flex; align-items:center; font-size:11px; margin-top:5px; padding:2px 7px; border-radius:10px; font-weight:500; }
.cg-metric-chip.red { background:#FEF2F2; color:#991B1B; }
.cg-metric-chip.green { background:#F0FDF4; color:#15803D; }
.cg-metric-chip.blue { background:#EFF6FF; color:#1E40AF; }

/* ── QUICK LOAD BUTTONS ───────────────────────────────────────────── */
[data-testid^="stButton-quick_"] > button {
    background:#F8FAFC !important; color:#1B2B45 !important;
    border:1px solid #E5E7EB !important; font-size:11px !important;
    font-weight:400 !important; padding:5px 10px !important;
    border-radius:6px !important; text-align:left !important;
    min-height:unset !important;
}
[data-testid^="stButton-quick_"] > button:hover {
    background:#EFF6FF !important; border-color:#BFDBFE !important; color:#1E40AF !important;
}

/* ── EMPTY STATE ──────────────────────────────────────────────────── */
.cg-empty { padding:48px 24px; text-align:center; background:white; }
</style>
""", unsafe_allow_html=True)

# ── Cached resources ──────────────────────────────────────────────────────────
@st.cache_resource(show_spinner="Loading fraud detection engine...")
def load_rule_engine():
    if not RULES_AVAILABLE: return None
    try: return RuleEngine(ncci_path=NCCI_PATH)
    except: return None

@st.cache_resource(show_spinner="Connecting to AI layer...")
def load_llm_checker():
    if not LLM_AVAILABLE or not os.environ.get("ANTHROPIC_API_KEY"): return None
    try: return LLMChecker()
    except: return None

@st.cache_data(show_spinner=False)
def load_all_claims():
    if not CLAIMS_JSON.exists(): return []
    with open(CLAIMS_JSON) as f: return json.load(f)

@st.cache_data(show_spinner=False)
def load_precomputed_results():
    """Load batch EDI evaluation results from all_results.json"""
    results_path = BASE_DIR / "data" / "final_results" / "all_results.json"
    if not results_path.exists(): return {}
    with open(results_path) as f:
        results = json.load(f)
    return {r["claim_id"]: r for r in results}

def run_fraud_check(claim, rule_engine, llm_checker):
    rule_result = rule_engine.check(claim) if rule_engine else {
        "claim_id": claim.get("claim_id","?"), "fraud_detected": False,
        "fraud_type": None, "explanation": "Rule engine unavailable.",
        "confidence": "low", "checked_by": "rule_engine"
    }
    if not rule_result.get("fraud_detected"):
        llm_result = (llm_checker.check(claim) if llm_checker else {
            "fraud_detected": False, "fraud_type": None, "confidence": "low",
            "explanation": "Set ANTHROPIC_API_KEY to enable AI analysis.",
            "checked_by": "llm_claude"
        })
    else:
        llm_result = {
            "fraud_detected": True, "fraud_type": rule_result.get("fraud_type"),
            "confidence": "high", "explanation": rule_result.get("explanation",""),
            "skipped": True, "checked_by": "rule_engine"
        }
    rf = rule_result.get("fraud_detected", False)
    lf = llm_result.get("fraud_detected", False)
    lc = llm_result.get("confidence","low")
    if rf:
        return {"fraud_detected":True,"fraud_type":rule_result.get("fraud_type"),
                "confidence":"High","explanation":rule_result.get("explanation",""),
                "engine":"Rule Engine"}, rule_result, llm_result
    elif lf and lc in ["high","medium","low"]:
        return {"fraud_detected":True,"fraud_type":llm_result.get("fraud_type"),
                "confidence":lc.capitalize(),"explanation":llm_result.get("explanation",""),
                "engine":"LLM Reasoning"}, rule_result, llm_result
    return {"fraud_detected":False,"fraud_type":None,"confidence":"High",
            "explanation":"All checks passed. No fraud indicators detected.",
            "engine":"Both"}, rule_result, llm_result

def normalize_raw(raw):
    p = raw.get("patient",{}); pr = raw.get("provider",{})
    return {
        "claim_id":raw.get("claim_id",""),"date":raw.get("date",""),
        "patient_name":p.get("name","") if isinstance(p,dict) else "",
        "provider_name":pr.get("name","") if isinstance(pr,dict) else "",
        "facility":pr.get("facility","") if isinstance(pr,dict) else "",
        "insurer":p.get("insurer","") if isinstance(p,dict) else "",
        "procedure_codes":raw.get("procedure_codes",[]),
        "procedure_descs":raw.get("procedure_descs",[]),
        "diagnosis_codes":raw.get("diagnosis_codes",[]),
        "diagnosis_descs":raw.get("diagnosis_descs",[]),
        "modifiers":raw.get("modifiers",[]),
        "line_charges":raw.get("line_charges",[]),
        "total_charge":raw.get("total_charge",0),
        "source":"dataset",
    }

# ── Verdict display ───────────────────────────────────────────────────────────
def render_verdict(final, rule_r=None, llm_r=None):
    if final["fraud_detected"]:
        ft = final.get("fraud_type","Unknown") or "Unknown"
        st.markdown(f"""
        <div style="border:1px solid #FECACA;border-left:4px solid #DC2626;
                    background:#FEF2F2;border-radius:8px;padding:20px 24px;margin:16px 0">
          <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">
            <span style="background:#DC2626;color:white;font-size:10px;font-weight:700;
                         letter-spacing:0.1em;text-transform:uppercase;
                         padding:3px 10px;border-radius:4px">Fraud Detected</span>
            <span style="font-size:17px;font-weight:600;color:#1B2B45">{ft}</span>
          </div>
          <p style="font-size:13px;color:#4B5563;line-height:1.75;margin:0">
            {final.get("explanation","")}
          </p>
          <div style="margin-top:12px;font-size:11px;color:#9CA3AF;display:flex;gap:20px">
            <span>Engine: <b style="color:#6B7280">{final.get("engine","—")}</b></span>
            <span>Confidence: <b style="color:#6B7280">{final.get("confidence","")}</b></span>
          </div>
        </div>""", unsafe_allow_html=True)
    else:
        st.markdown(f"""
        <div style="border:1px solid #BBF7D0;border-left:4px solid #16A34A;
                    background:#F0FDF4;border-radius:8px;padding:20px 24px;margin:16px 0">
          <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">
            <span style="background:#16A34A;color:white;font-size:10px;font-weight:700;
                         letter-spacing:0.1em;text-transform:uppercase;
                         padding:3px 10px;border-radius:4px">Legitimate</span>
            <span style="font-size:17px;font-weight:600;color:#1B2B45">No Fraud Detected</span>
          </div>
          <p style="font-size:13px;color:#4B5563;line-height:1.75;margin:0">
            {final.get("explanation","All checks passed.")}
          </p>
          <div style="margin-top:12px;font-size:11px;color:#9CA3AF">
            Reviewed by: <b style="color:#6B7280">{final.get("engine","Both")}</b>
            &nbsp;·&nbsp; Confidence: <b style="color:#6B7280">HIGH</b>
          </div>
        </div>""", unsafe_allow_html=True)
    with st.expander("Engine details"):
        c1,c2 = st.columns(2)
        with c1:
            st.markdown("**Rule Engine**")
            if rule_r:
                st.write("Status:","Flagged ⚠️" if rule_r.get("fraud_detected") else "Clear ✓")
                if rule_r.get("fraud_type"): st.write("Type:",rule_r["fraud_type"])
                if rule_r.get("explanation"): st.caption(rule_r["explanation"][:300])
        with c2:
            st.markdown("**LLM Reasoning Layer**")
            if llm_r:
                if llm_r.get("skipped"):
                    st.write("Skipped — rule engine already flagged.")
                else:
                    st.write("Status:","Flagged ⚠️" if llm_r.get("fraud_detected") else "Clear ✓")
                    if llm_r.get("fraud_type"): st.write("Type:",llm_r["fraud_type"])
                    st.write("Confidence:",llm_r.get("confidence","").upper())
                    if llm_r.get("explanation"): st.caption(llm_r["explanation"][:300])

# ── Claim detail modal ────────────────────────────────────────────────────────
def render_claim_modal(claim_data, rule_engine, llm_checker):
    """Shows a detailed modal for a selected claim with fraud analysis."""
    is_fraud = claim_data.get("fraud_indicator", False)
    ft = claim_data.get("fraud_type","Legitimate")
    cid = claim_data.get("claim_id","")

    patient  = claim_data.get("patient",{})
    provider = claim_data.get("provider",{})
    pat_name = patient.get("name","—") if isinstance(patient,dict) else "—"
    pat_id   = patient.get("id","") if isinstance(patient,dict) else ""
    prov_nm  = provider.get("name","—") if isinstance(provider,dict) else "—"

    cpt      = claim_data.get("procedure_codes",[])
    cpt_desc = claim_data.get("procedure_descs",[])
    icd      = claim_data.get("diagnosis_codes",[])
    icd_desc = claim_data.get("diagnosis_descs",[])
    mods     = claim_data.get("modifiers",[])
    total    = claim_data.get("total_charge",0)

    badge_color = "#DC2626" if is_fraud else "#16A34A"
    badge_bg    = "#FEF2F2" if is_fraud else "#F0FDF4"

    st.markdown(f"""
    <div style="background:#1B2B45;padding:16px 24px;border-radius:8px 8px 0 0;
                display:flex;align-items:center;justify-content:space-between">
      <div>
        <span style="font-size:16px;font-weight:600;color:white">{cid}</span>
        <span style="font-size:13px;color:#9CA3AF;margin-left:12px">— {ft}</span>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # Two-column claim details
    col1, col2 = st.columns(2)
    fields_l = [
        ("CLAIM ID", cid),
        ("PATIENT", f"{pat_name} ({pat_id})" if pat_id else pat_name),
        ("PROCEDURE CODE(S)", " · ".join(cpt) if cpt else "—"),
        ("DIAGNOSIS CODE", " · ".join(icd) if icd else "—"),
        ("MODIFIER", mods[0] if mods else "—"),
    ]
    fields_r = [
        ("DATE", claim_data.get("date","—")),
        ("PROVIDER", prov_nm),
        ("PROCEDURE", cpt_desc[0] if cpt_desc else "—"),
        ("DIAGNOSIS", icd_desc[0] if icd_desc else "—"),
        ("BILLED AMOUNT", f"${total:,.2f}"),
    ]

    def field_html(lbl, val):
        return f"""
        <div style="margin-bottom:16px">
          <div style="font-size:10px;font-weight:600;color:#9CA3AF;letter-spacing:0.1em;
                      text-transform:uppercase;margin-bottom:3px">{lbl}</div>
          <div style="font-size:14px;color:#1B2B45;font-weight:{"600" if lbl in ["BILLED AMOUNT","CLAIM ID"] else "400"};
                      font-family:{"'DM Mono',monospace" if lbl in ["PROCEDURE CODE(S)","DIAGNOSIS CODE","CLAIM ID","BILLED AMOUNT"] else "inherit"}">{val}</div>
        </div>"""

    with col1:
        st.markdown("".join([field_html(l,v) for l,v in fields_l]), unsafe_allow_html=True)
    with col2:
        st.markdown("".join([field_html(l,v) for l,v in fields_r]), unsafe_allow_html=True)

    # Fraud verdict section
    expl = claim_data.get("fraud_explanation","") or ""
    if is_fraud:
        st.markdown(f"""
        <div style="background:{badge_bg};border:1px solid #FECACA;
                    border-left:3px solid {badge_color};
                    border-radius:6px;padding:14px 18px">
          <div style="font-size:11px;font-weight:700;color:{badge_color};
                      letter-spacing:0.1em;text-transform:uppercase;margin-bottom:6px">
            Fraud Type: {ft}
          </div>
          <div style="font-size:13px;color:#4B5563;line-height:1.7">{expl}</div>
        </div>""", unsafe_allow_html=True)
    else:
        st.markdown(f"""
        <div style="background:#F0FDF4;border:1px solid #BBF7D0;
                    border-left:3px solid #16A34A;
                    border-radius:6px;padding:14px 18px">
          <div style="font-size:11px;font-weight:700;color:#16A34A;
                      letter-spacing:0.1em;text-transform:uppercase;margin-bottom:6px">
            Fraud Type: Legitimate
          </div>
          <div style="font-size:13px;color:#4B5563;line-height:1.7">
            This claim appears consistent. Procedure codes match the diagnosis and amounts are within expected range.
          </div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    # Run live analysis button
    if st.button("Run Live Fraud Analysis", key=f"modal_analyse_{cid}", type="primary"):
        claim = normalize_raw(claim_data)
        with st.spinner("Analysing..."):
            final, rr, lr = run_fraud_check(claim, rule_engine, llm_checker)
        render_verdict(final, rr, lr)

# ── Claims table ──────────────────────────────────────────────────────────────
def render_claims_table(all_claims, search_q, fraud_filter, rule_engine, llm_checker):
    filtered = all_claims
    if fraud_filter and fraud_filter != "All Claims":
        filtered = [c for c in filtered if c.get("fraud_type","Legitimate") == fraud_filter]
    if search_q:
        q = search_q.lower()
        filtered = [c for c in filtered if
                    q in c.get("claim_id","").lower() or
                    q in (c.get("patient",{}).get("name","") if isinstance(c.get("patient"),dict) else "").lower() or
                    q in (c.get("provider",{}).get("name","") if isinstance(c.get("provider"),dict) else "").lower() or
                    any(q in cd.lower() for cd in c.get("procedure_codes",[])) or
                    any(q in cd.lower() for cd in c.get("diagnosis_codes",[]))]

    total_f = len(filtered)
    fraud_f = sum(1 for c in filtered if c.get("fraud_indicator"))
    exp_f   = sum(c.get("total_charge",0) for c in filtered if c.get("fraud_indicator"))

    # Header bar
    st.markdown(f"""
    <div style="background:white;border-bottom:1px solid #E5E7EB;padding:12px 24px;
                display:flex;align-items:center;justify-content:space-between">
      <span style="font-size:14px;font-weight:600;color:#1B2B45">Claims Registry</span>
      <div style="display:flex;align-items:center;gap:20px;font-size:12px;color:#6B7280">
        <span>{total_f:,} claims</span>
        <span style="color:#DC2626;font-weight:500">{fraud_f:,} flagged</span>
        <span>Fraud exposure: <span style="font-family:'DM Mono',monospace;color:#DC2626;font-weight:600">${exp_f:,.2f}</span></span>
      </div>
    </div>""", unsafe_allow_html=True)

    # Column header — 8 columns
    GRID = "130px 100px 1fr 110px 1fr 110px 150px 55px 36px"
    HDR  = ["CLAIM ID","DATE","PATIENT","CPT CODE","DIAGNOSIS","BILLED AMT","FRAUD TYPE","FLAG",""]
    hdr_cells = "".join([
        f'<div style="font-size:10px;font-weight:600;color:#6B7280;letter-spacing:0.08em;'
        f'text-transform:uppercase;white-space:nowrap;overflow:hidden">{h}</div>'
        for h in HDR])
    st.markdown(f"""
    <div style="background:#F8FAFC;padding:9px 24px;border-bottom:1px solid #E5E7EB;
                display:grid;grid-template-columns:{GRID};gap:16px;align-items:center">
      {hdr_cells}
    </div>""", unsafe_allow_html=True)

    BADGE = {
        "Legitimate":           ("#DCFCE7","#15803D"),
        "Upcoding":             ("#FEE2E2","#DC2626"),
        "Code Padding":         ("#FEE2E2","#DC2626"),
        "Phantom Billing":      ("#FEE2E2","#DC2626"),
        "Diagnosis Mismatch":   ("#FEF9C3","#854D0E"),
        "Code Substitution":    ("#FEF9C3","#854D0E"),
        "Modifier Abuse (-59)": ("#FEF9C3","#854D0E"),
        "Duplicate Billing":    ("#DBEAFE","#1D4ED8"),
        "Unbundling":           ("#DBEAFE","#1D4ED8"),
        "Screening Code Abuse": ("#DBEAFE","#1D4ED8"),
    }
    BADGE_LBL = {
        "Legitimate":"Legitimate","Upcoding":"Upcoding","Code Padding":"Code Padding",
        "Phantom Billing":"Phantom Billing","Diagnosis Mismatch":"Diag. Mismatch",
        "Code Substitution":"Code Sub.","Modifier Abuse (-59)":"Mod. Abuse",
        "Duplicate Billing":"Duplicate","Unbundling":"Unbundling",
        "Screening Code Abuse":"Screening",
    }

    # Render rows — HTML row (visual) + invisible button (click handler)
    for i, c in enumerate(filtered[:50]):
        is_fraud = c.get("fraud_indicator", False)
        ft       = c.get("fraud_type","Legitimate")
        row_bg   = "white"
        row_bdr  = "#F3F4F6"

        patient  = c.get("patient",{})
        pat_name = patient.get("name","—") if isinstance(patient,dict) else "—"
        pat_id   = patient.get("id","") if isinstance(patient,dict) else ""
        cpt      = c.get("procedure_codes",[])
        icd      = c.get("diagnosis_codes",[])
        icdds    = c.get("diagnosis_descs",[])

        cpt_txt  = cpt[0] if cpt else "—"
        icd_txt  = icd[0] if icd else "—"
        icd_desc = icdds[0][:26]+"…" if icdds and len(icdds[0])>26 else (icdds[0] if icdds else "")
        bb,bc    = BADGE.get(ft,("#F3F4F6","#374151"))
        bl       = BADGE_LBL.get(ft,ft[:14])
        flag     = '<span style="display:inline-flex;align-items:center;gap:5px"><span style="width:7px;height:7px;border-radius:50%;background:#DC2626;display:inline-block;flex-shrink:0"></span><span style="font-size:12px;font-weight:500;color:#DC2626">Yes</span></span>' if is_fraud else '<span style="display:inline-flex;align-items:center;gap:5px"><span style="width:7px;height:7px;border-radius:50%;background:#16A34A;display:inline-block;flex-shrink:0"></span><span style="font-size:12px;color:#16A34A">No</span></span>'
        cid      = c.get("claim_id","")

        # Visual row — pure HTML, no interactivity
        st.markdown(f"""
        <div style="background:{row_bg};border-bottom:1px solid {row_bdr};
                    padding:10px 24px;display:grid;grid-template-columns:{GRID};
                    gap:16px;align-items:center;min-height:46px">
          <div style="font-family:'DM Mono',monospace;font-size:12px;font-weight:500;
                      color:#1B2B45;white-space:nowrap">{cid}</div>
          <div style="font-size:12px;color:#6B7280;white-space:nowrap">{c.get("date","")}</div>
          <div style="min-width:0">
            <div style="font-size:13px;font-weight:500;color:#1B2B45;white-space:nowrap;
                        overflow:hidden;text-overflow:ellipsis">{pat_name}</div>
            <div style="font-size:11px;color:#9CA3AF;margin-top:1px">{pat_id}</div>
          </div>
          <div style="font-family:'DM Mono',monospace;font-size:12px;color:#2563EB;
                      font-weight:500;white-space:nowrap">{cpt_txt}</div>
          <div style="min-width:0">
            <div style="font-family:'DM Mono',monospace;font-size:12px;color:#7C3AED;
                        font-weight:500;white-space:nowrap">{icd_txt}</div>
            <div style="font-size:11px;color:#9CA3AF;white-space:nowrap;
                        overflow:hidden;text-overflow:ellipsis;margin-top:1px">{icd_desc}</div>
          </div>
          <div style="font-family:'DM Mono',monospace;font-size:12px;
                      font-weight:500;color:#1B2B45;white-space:nowrap">${c.get("total_charge",0):,.2f}</div>
          <div><span style="background:{bb};color:{bc};font-size:10px;font-weight:600;
                             padding:3px 9px;border-radius:4px;letter-spacing:0.03em;
                             white-space:nowrap;display:inline-block">{bl}</span></div>
          <div>{flag}</div>
          <div style="font-size:13px;color:#CBD5E1;text-align:center">›</div>
        </div>""", unsafe_allow_html=True)

        # Functional button — negative margin overlays the row above, transparent
        if st.button(f"{cid}", key=f"row_CLM_{cid}_{i}", use_container_width=True):
            st.session_state.selected_claim = c
            st.session_state.show_modal = True
            st.rerun()

    if len(filtered) == 0:
        st.markdown("""
        <div class="cg-empty" style="background:white;border-bottom:1px solid #F3F4F6;padding:48px 24px;text-align:center">
          <div style="font-size:28px;margin-bottom:12px">🔍</div>
          <div style="font-size:14px;font-weight:500;color:#6B7280;margin-bottom:4px">No claims match your search</div>
          <div style="font-size:12px;color:#9CA3AF">Try a different search term or clear the active filter</div>
        </div>""", unsafe_allow_html=True)
    elif len(filtered) > 50:
        st.markdown(f"""
        <div style="padding:10px 24px;background:white;border-top:1px solid #E5E7EB;
                    font-size:12px;color:#9CA3AF">
          Showing first 50 of {len(filtered):,} — use search or filter to narrow results.
        </div>""", unsafe_allow_html=True)


# ── Submit view ───────────────────────────────────────────────────────────────
def render_submit(rule_engine, llm_checker, all_claims):
    # Initialise session state keys for submit page
    for k, v in [("edi_result", None), ("edi_claim", None),
                 ("sample_result", None), ("sample_claim", None),
                 ("sample_sel", None)]:
        if k not in st.session_state:
            st.session_state[k] = v

    # Top bar
    col_back, col_title = st.columns([1, 8])
    with col_back:
        if st.button("← Home", key="submit_home"):
            st.session_state.view = "table"; st.rerun()
    with col_title:
        st.markdown("""
        <div style="padding:8px 0">
          <span style="font-size:14px;font-weight:600;color:#1B2B45">Submit Claim for Analysis</span>
        </div>""", unsafe_allow_html=True)
    st.markdown('<div style="border-bottom:1px solid #E5E7EB;margin:0 0 4px 0"></div>',
                unsafe_allow_html=True)

    tab1, tab3 = st.tabs(["  EDI 837P Upload  ", "  Sample from Dataset  "])

    # ── EDI TAB ────────────────────────────────────────────────────────────────
    with tab1:
        st.markdown("<div style='padding:24px 24px 0'>", unsafe_allow_html=True)
        col1, col2 = st.columns([1, 1], gap="large")

        with col1:
            # Info card
            st.markdown("""
            <div style="background:white;border:1px solid #E5E7EB;border-radius:8px;
                        padding:20px 24px;margin-bottom:16px">
              <div style="font-size:13px;font-weight:600;color:#1B2B45;margin-bottom:6px">
                EDI 837P Electronic Claim
              </div>
              <div style="font-size:12px;color:#6B7280;line-height:1.6;margin-bottom:16px">
                The X12 837P format is used by approximately 95% of US insurance claims
                under the HIPAA Electronic Transactions Standard. Upload a .edi or .txt
                file containing a valid 837P transaction set.
              </div>
              <div style="background:#F9FAFB;border:1px solid #E5E7EB;border-radius:6px;
                          padding:12px 16px;font-family:'DM Mono',monospace;font-size:11px;
                          color:#6B7280;line-height:1.8">
                ISA*00*&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;*ZZ*FRAUDDETECT...<br>
                HI*ABK:J06.9~<br>
                SV1*HC:99213*92.20*UN*1***A~
              </div>
            </div>""", unsafe_allow_html=True)

            # Quick-load demo files
            st.markdown("""
            <div style="font-size:10px;font-weight:600;color:#9CA3AF;letter-spacing:0.08em;
                        text-transform:uppercase;margin-bottom:6px">Quick load demo claim</div>""",
                unsafe_allow_html=True)
            demo_files = {
                "CLM99001 — Phantom Billing":    "CLM99001.edi",
                "CLM99002 — Duplicate Billing":  "CLM99002.edi",
                "CLM99003 — Modifier Abuse":     "CLM99003.edi",
                "CLM99004 — Legitimate claim":   "CLM99004.edi",
            }
            qcols = st.columns(2)
            for qi, (label, fname) in enumerate(demo_files.items()):
                with qcols[qi % 2]:
                    edi_path = BASE_DIR / "data" / "edi_new" / fname
                    if st.button(label, key=f"quick_{fname}", use_container_width=True,
                                 disabled=not edi_path.exists()):
                        try:
                            claim = parse_edi_file(edi_path)
                            if claim and claim.get("procedure_codes"):
                                precomputed  = load_precomputed_results()
                                cid          = claim.get("claim_id","")
                                batch_result = precomputed.get(cid)
                                if batch_result:
                                    bf  = batch_result.get("fraud_detected", False)
                                    bt  = batch_result.get("fraud_type") or "None"
                                    bc  = (batch_result.get("confidence") or "high").upper()
                                    be  = batch_result.get("explanation","") or "All checks passed."
                                    bdb = batch_result.get("decision_by","rule_engine")
                                    final = {"fraud_detected":bf,"fraud_type":bt if bf else None,
                                             "confidence":bc,"explanation":be,
                                             "engine":bdb.replace("_"," ").title()}
                                    decided_by_rule = "rule" in bdb.lower()
                                    if bf and decided_by_rule:
                                        rr = {"fraud_detected":True,"fraud_type":bt,"explanation":be}
                                        lr = {"fraud_detected":False,"confidence":"high","explanation":"Skipped.","skipped":True}
                                    elif bf:
                                        rr = {"fraud_detected":False,"explanation":"All rule checks passed."}
                                        lr = {"fraud_detected":True,"fraud_type":bt,"confidence":bc.lower(),"explanation":be}
                                    else:
                                        rr = {"fraud_detected":False,"explanation":"All rule checks passed."}
                                        lr = {"fraud_detected":False,"confidence":"high","explanation":"No fraud found."}
                                else:
                                    with st.status("Running live analysis…", expanded=True) as qs:
                                        st.write("Running rule-based checks…")
                                        final, rr, lr = run_fraud_check(claim, rule_engine, llm_checker)
                                        st.write("AI reasoning complete.")
                                        qs.update(label="Complete", state="complete", expanded=False)
                                st.session_state.edi_claim  = claim
                                st.session_state.edi_result = (final, rr, lr, batch_result is not None)
                                st.rerun()
                        except Exception as e:
                            st.error(f"Could not load {fname}: {e}")
            st.markdown("<div style='margin-bottom:12px'></div>", unsafe_allow_html=True)

            # File uploader — always visible
            f = st.file_uploader("Or upload your own EDI file (.edi, .txt)", type=["edi", "txt"],
                                 key="edi_up", label_visibility="visible")

            if f:
                st.markdown(f"""
                <div style="background:#F0FDF4;border:1px solid #BBF7D0;border-radius:6px;
                            padding:10px 14px;font-size:12px;color:#166534;margin:10px 0 12px">
                  ✓ &nbsp;File loaded: <b>{f.name}</b> — ready to analyse
                </div>""", unsafe_allow_html=True)

            # Button always rendered when file present (outside nested ifs to stay visible)
            run_edi = st.button(
                "▶  Run Fraud Analysis" if f else "Upload a file to analyse",
                key="go_edi",
                use_container_width=True,
                disabled=(f is None),
            )

            if run_edi and f:
                with tempfile.NamedTemporaryFile(mode="wb", suffix=".edi", delete=False) as tmp:
                    tmp.write(f.read())
                    tmp_path = Path(tmp.name)
                try:
                    claim = parse_edi_file(tmp_path)
                    if not claim or not claim.get("procedure_codes"):
                        st.error("Unable to parse EDI file. Verify X12 837P format.")
                        st.session_state.edi_claim = None
                        st.session_state.edi_result = None
                    else:
                        precomputed  = load_precomputed_results()
                        cid          = claim.get("claim_id", "")
                        batch_result = precomputed.get(cid)
                        if batch_result:
                            bf  = batch_result.get("fraud_detected", False)
                            bt  = batch_result.get("fraud_type") or "None"
                            bc  = (batch_result.get("confidence") or "high").upper()
                            be  = batch_result.get("explanation", "") or "All checks passed."
                            bdb = batch_result.get("decision_by", "rule_engine")
                            final = {"fraud_detected": bf, "fraud_type": bt if bf else None,
                                     "confidence": bc, "explanation": be,
                                     "engine": bdb.replace("_", " ").title()}
                            # Reconstruct rule/llm sub-results from decision_by — stale
                            # sub-fields in old batch JSON are unreliable
                            decided_by_rule = "rule" in bdb.lower()
                            if bf and decided_by_rule:
                                rr = {"fraud_detected": True, "fraud_type": bt, "explanation": be}
                                lr = {"fraud_detected": False, "confidence": "high",
                                      "explanation": "Skipped — rule engine already flagged.", "skipped": True}
                            elif bf and not decided_by_rule:
                                rr = {"fraud_detected": False,
                                      "explanation": "All rule-based checks passed."}
                                lr = {"fraud_detected": True, "fraud_type": bt,
                                      "confidence": bc.lower(), "explanation": be}
                            else:
                                rr = {"fraud_detected": False,
                                      "explanation": "All rule-based checks passed. No duplicate billing, unbundling, modifier abuse, or screening code abuse detected."}
                                lr = {"fraud_detected": False, "confidence": "high",
                                      "explanation": "No fraud indicators found by LLM analysis."}
                        else:
                            with st.status("Running fraud analysis…", expanded=True) as status:
                                st.write("Parsing EDI claim fields…")
                                st.write("Running rule-based checks (NCCI, modifiers, bundling)…")
                                final, rr, lr = run_fraud_check(claim, rule_engine, llm_checker)
                                st.write("AI reasoning layer complete." if llm_checker else "AI layer skipped (no API key).")
                                status.update(label="Analysis complete", state="complete", expanded=False)
                        st.session_state.edi_claim  = claim
                        st.session_state.edi_result = (final, rr, lr, batch_result is not None)
                finally:
                    tmp_path.unlink(missing_ok=True)

        # Right column — results (always rendered so they persist across reruns)
        with col2:
            if st.session_state.edi_result:
                final, rr, lr, was_batch = st.session_state.edi_result
                claim = st.session_state.edi_claim
                _show_extracted_fields(claim)
                if not was_batch:
                    st.markdown("""
                    <div style="background:#FFF7ED;border:1px solid #FED7AA;border-radius:6px;
                                padding:10px 14px;font-size:12px;color:#92400E;margin-bottom:12px">
                      ℹ️ New claim — no batch result. Showing live analysis.
                    </div>""", unsafe_allow_html=True)
                render_verdict(final, rr, lr)
            else:
                st.markdown("""
                <div style="background:white;border:1px solid #E5E7EB;border-radius:8px;
                            padding:40px 24px;text-align:center;color:#9CA3AF;font-size:13px">
                  <div style="font-size:28px;margin-bottom:10px">🛡️</div>
                  Upload an EDI file and click <b>Run Fraud Analysis</b><br>to see results here.
                </div>""", unsafe_allow_html=True)

        st.markdown("</div>", unsafe_allow_html=True)

    # ── SAMPLE TAB ─────────────────────────────────────────────────────────────
    with tab3:
        st.markdown("<div style='padding:24px 24px 0'>", unsafe_allow_html=True)
        col1, col2 = st.columns([1, 1], gap="large")

        with col1:
            st.markdown("""
            <div style="background:white;border:1px solid #E5E7EB;border-radius:8px;
                        padding:20px 24px;margin-bottom:16px">
              <div style="font-size:13px;font-weight:600;color:#1B2B45;margin-bottom:6px">
                Evaluation Dataset Sample
              </div>
              <div style="font-size:12px;color:#6B7280;line-height:1.6">
                Select a claim from the 1,300-claim evaluation dataset. Ground truth labels
                are shown for comparison against the system's real-time output.
              </div>
            </div>""", unsafe_allow_html=True)

            options = {}
            for ft in ["Legitimate", "Duplicate Billing", "Modifier Abuse (-59)",
                       "Code Padding", "Phantom Billing", "Diagnosis Mismatch",
                       "Screening Code Abuse", "Upcoding", "Unbundling", "Code Substitution"]:
                c2 = next((x for x in all_claims if x.get("fraud_type") == ft), None)
                if c2:
                    ind = "⚠  " if c2.get("fraud_indicator") else "○  "
                    options[f"{ind}{c2['claim_id']}  —  {ft}"] = c2

            sel_label = st.selectbox("Select claim:", list(options.keys()),
                                     label_visibility="visible", key="sample_select")
            sel = options[sel_label]

            # Reset results when selection changes
            if st.session_state.sample_sel != sel_label:
                st.session_state.sample_sel    = sel_label
                st.session_state.sample_result = None
                st.session_state.sample_claim  = None

            is_fraud_gt = sel.get("fraud_indicator", False)
            gt_bg  = "#FEF2F2" if is_fraud_gt else "#F0FDF4"
            gt_bdr = "#FECACA" if is_fraud_gt else "#BBF7D0"
            gt_col = "#B91C1C" if is_fraud_gt else "#166534"
            gt_ttl = sel.get("fraud_type", "Legitimate")
            gt_exp = (sel.get("fraud_explanation") or "")[:120]
            gt_txt = f"{gt_ttl} — {gt_exp}" if is_fraud_gt else "Legitimate claim — no fraud present."

            st.markdown(f"""
            <div style="background:{gt_bg};border:1px solid {gt_bdr};
                        border-left:3px solid {gt_col};
                        border-radius:6px;padding:12px 16px;margin:12px 0">
              <div style="font-size:10px;font-weight:700;color:{gt_col};
                          letter-spacing:0.1em;text-transform:uppercase;margin-bottom:4px">
                Ground Truth
              </div>
              <div style="font-size:12px;color:{gt_col}">{gt_txt}</div>
            </div>""", unsafe_allow_html=True)

            if st.button("▶  Run Fraud Analysis", key="go_sample", use_container_width=True):
                claim = normalize_raw(sel)
                with st.status("Running fraud analysis…", expanded=True) as status:
                    st.write("Loading claim data…")
                    st.write("Running rule-based checks (NCCI, modifiers, bundling)…")
                    final, rr, lr = run_fraud_check(claim, rule_engine, llm_checker)
                    st.write("AI reasoning complete." if llm_checker else "AI layer skipped (no API key).")
                    status.update(label="Analysis complete", state="complete", expanded=False)
                true_fraud = sel.get("fraud_indicator", False)
                match = (final["fraud_detected"] == true_fraud)
                fn    = true_fraud and not final["fraud_detected"]
                fp    = not true_fraud and final["fraud_detected"]
                st.session_state.sample_claim  = claim
                st.session_state.sample_result = (final, rr, lr, match, fn, fp)

        # Right column — results persist
        with col2:
            if st.session_state.sample_result:
                final, rr, lr, match, fn, fp = st.session_state.sample_result
                _show_claim_card(st.session_state.sample_claim)
                render_verdict(final, rr, lr)
                if match:
                    st.success("✓ Prediction matches ground truth.")
                elif fn:
                    st.warning("False negative — fraud not detected.")
                else:
                    st.warning("False positive — legitimate claim flagged.")
            else:
                st.markdown("""
                <div style="background:white;border:1px solid #E5E7EB;border-radius:8px;
                            padding:40px 24px;text-align:center;color:#9CA3AF;font-size:13px">
                  <div style="font-size:28px;margin-bottom:10px">🔍</div>
                  Select a claim and click <b>Run Fraud Analysis</b><br>to see the verdict here.
                </div>""", unsafe_allow_html=True)

        st.markdown("</div>", unsafe_allow_html=True)



def _show_extracted_fields(claim):
    """Shows extracted fields from EDI/PDF in a clean card."""
    cpt = claim.get("procedure_codes",[])
    icd = claim.get("diagnosis_codes",[])
    st.markdown("""
    <div style="font-size:13px;font-weight:600;color:#1B2B45;margin-bottom:12px">
      Extracted Claim Fields
    </div>""", unsafe_allow_html=True)
    rows = [
        ("Claim ID",    claim.get("claim_id","—"),    True),
        ("Patient",     claim.get("patient_name","—"), False),
        ("Provider",    claim.get("provider_name","—"),False),
        ("Date",        claim.get("date","—"),          False),
        ("CPT Codes",   " · ".join(cpt) if cpt else "—", True),
        ("ICD-10",      " · ".join(icd) if icd else "—", True),
        ("Modifiers",   " · ".join(claim.get("modifiers",[])) or "None", True),
        ("Total Billed",f"${claim.get('total_charge',0):,.2f}", True),
        ("Source",      claim.get("source","").upper().replace("_"," "), False),
    ]
    _render_detail_table(rows)


def _show_claim_card(claim):
    """Shows claim fields for sample dataset claims."""
    cpt = claim.get("procedure_codes",[])
    icd = claim.get("diagnosis_codes",[])
    st.markdown("""
    <div style="font-size:13px;font-weight:600;color:#1B2B45;margin-bottom:12px">
      Claim Details
    </div>""", unsafe_allow_html=True)
    rows = [
        ("Claim ID",    claim.get("claim_id","—"),    True),
        ("Patient",     claim.get("patient_name","—"), False),
        ("Provider",    claim.get("provider_name","—"),False),
        ("Facility",    claim.get("facility","—"),     False),
        ("Insurer",     claim.get("insurer","—"),      False),
        ("CPT Codes",   " · ".join(cpt) if cpt else "—", True),
        ("ICD-10",      " · ".join(icd) if icd else "—", True),
        ("Total Billed",f"${claim.get('total_charge',0):,.2f}", True),
    ]
    _render_detail_table(rows)


def _render_detail_table(rows):
    table_rows = ""
    for i,(lbl,val,mono) in enumerate(rows):
        bg = "#F9FAFB" if i%2==0 else "white"
        mc = "font-family:'DM Mono',monospace;color:#2563EB;" if mono else ""
        fw = "font-weight:600;" if lbl in ["Total Billed","Claim ID"] else ""
        table_rows += f"""<tr style="background:{bg}">
          <td style="padding:9px 16px;font-size:11px;font-weight:600;color:#9CA3AF;
                     width:120px;border-bottom:1px solid #F3F4F6;
                     text-transform:uppercase;letter-spacing:0.06em;white-space:nowrap">{lbl}</td>
          <td style="padding:9px 16px;font-size:13px;{mc}{fw}
                     border-bottom:1px solid #F3F4F6;color:#1B2B45">{val}</td>
        </tr>"""
    st.markdown(f"""
    <div style="background:white;border:1px solid #E5E7EB;border-radius:8px;
                overflow:hidden;margin-bottom:16px">
      <table style="width:100%;border-collapse:collapse">
        <tbody>{table_rows}</tbody>
      </table>
    </div>""", unsafe_allow_html=True)


# ── Performance view ──────────────────────────────────────────────────────────
def render_performance():
    col_back, col_title = st.columns([1, 8])
    with col_back:
        if st.button("← Home", key="perf_home"):
            st.session_state.view = "table"; st.rerun()
    with col_title:
        st.markdown("""
        <div style="padding:8px 0">
          <span style="font-size:14px;font-weight:600;color:#1B2B45">System Performance</span>
        </div>""", unsafe_allow_html=True)
    st.markdown('<div style="border-bottom:1px solid #E5E7EB;margin:0 0 4px 0"></div>',
                unsafe_allow_html=True)
    st.markdown("<div style='padding:24px'>", unsafe_allow_html=True)

    # Metric cards
    c1,c2,c3,c4 = st.columns(4,gap="medium")
    for col,lbl,val,sub,accent in [
        (c1,"F1 SCORE","0.763","Target ≥ 0.75  ✓","#16A34A"),
        (c2,"PRECISION","88.8%","Of flags, % correct","#2563EB"),
        (c3,"RECALL",   "66.9%","% of fraud caught",  "#2563EB"),
        (c4,"ACCURACY", "71.2%","Overall classification","#2563EB"),
    ]:
        with col:
            st.markdown(f"""
            <div style="background:white;border:1px solid #E5E7EB;border-radius:8px;
                        padding:22px 24px;border-top:3px solid {accent}">
              <div style="font-size:10px;font-weight:600;color:#9CA3AF;text-transform:uppercase;
                          letter-spacing:0.1em;margin-bottom:10px">{lbl}</div>
              <div style="font-size:32px;font-weight:300;color:#1B2B45;
                          font-family:'DM Mono',monospace;line-height:1">{val}</div>
              <div style="font-size:11px;color:#9CA3AF;margin-top:8px">{sub}</div>
            </div>""", unsafe_allow_html=True)

    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)
    col_l,col_r = st.columns([3,2],gap="large")

    with col_l:
        st.markdown("""<div style="font-size:11px;font-weight:600;color:#9CA3AF;letter-spacing:0.1em;
                        text-transform:uppercase;margin-bottom:14px">Per Fraud Type Performance</div>""",
                    unsafe_allow_html=True)
        perf = [
            ("Duplicate Billing",    "Rule",  80,  80,  1.000),
            ("Modifier Abuse (−59)", "Rule",  100, 100, 1.000),
            ("Code Padding",         "LLM",   100, 89,  0.877),
            ("Screening Code Abuse", "Rule",  80,  80,  0.755),
            ("Phantom Billing",      "LLM",   100, 65,  0.739),
            ("Diagnosis Mismatch",   "LLM",   120, 98,  0.638),
            ("Upcoding",             "LLM",   120, 44,  0.359),
            ("Unbundling",           "Rule",  100, 27,  0.353),
            ("Code Substitution",    "LLM",   100, 19,  0.304),
        ]
        rows = ""
        for i,(name,m,n,d,f1) in enumerate(perf):
            bg  = "#F9FAFB" if i%2==0 else "white"
            # Bar color = method (blue=Rule, purple=LLM), score chip = green/amber
            bar_color = "#2563EB" if m=="Rule" else "#7C3AED"
            score_color = "#16A34A" if f1>=0.75 else "#D97706"
            mb  = "#DBEAFE" if m=="Rule" else "#EDE9FE"
            mc2 = "#1D4ED8" if m=="Rule" else "#6D28D9"
            rows += f"""<tr style="background:{bg}">
              <td style="padding:10px 16px;font-size:13px;color:#1B2B45;border-bottom:1px solid #F3F4F6">{name}</td>
              <td style="padding:10px 16px;border-bottom:1px solid #F3F4F6">
                <span style="background:{mb};color:{mc2};font-size:10px;font-weight:600;padding:2px 8px;border-radius:4px">{m}</span></td>
              <td style="padding:10px 16px;text-align:right;font-family:'DM Mono',monospace;font-size:12px;color:#6B7280;border-bottom:1px solid #F3F4F6">{n}</td>
              <td style="padding:10px 16px;text-align:right;font-family:'DM Mono',monospace;font-size:12px;color:#6B7280;border-bottom:1px solid #F3F4F6">{d}</td>
              <td style="padding:10px 20px;border-bottom:1px solid #F3F4F6;min-width:160px">
                <div style="display:flex;align-items:center;gap:10px">
                  <div style="flex:1;height:5px;background:#E5E7EB;border-radius:3px;position:relative;min-width:70px">
                    <div style="position:absolute;left:0;top:0;height:100%;border-radius:3px;width:{int(f1*100)}%;background:{bar_color}"></div>
                  </div>
                  <span style="font-family:'DM Mono',monospace;font-size:12px;color:{score_color};font-weight:600;min-width:36px">{f1:.3f}</span>
                </div>
              </td>
            </tr>"""
        st.markdown(f"""
        <div style="background:white;border:1px solid #E5E7EB;border-radius:8px;overflow:hidden">
          <table style="width:100%;border-collapse:collapse">
            <thead><tr style="background:#F9FAFB;border-bottom:2px solid #E5E7EB">
              <th style="padding:10px 16px;text-align:left;font-size:10px;font-weight:600;color:#9CA3AF;letter-spacing:0.08em;text-transform:uppercase">Fraud Type</th>
              <th style="padding:10px 16px;text-align:left;font-size:10px;font-weight:600;color:#9CA3AF;letter-spacing:0.08em;text-transform:uppercase">Method</th>
              <th style="padding:10px 16px;text-align:right;font-size:10px;font-weight:600;color:#9CA3AF;letter-spacing:0.08em;text-transform:uppercase">Expected</th>
              <th style="padding:10px 16px;text-align:right;font-size:10px;font-weight:600;color:#9CA3AF;letter-spacing:0.08em;text-transform:uppercase">Detected</th>
              <th style="padding:10px 20px;text-align:left;font-size:10px;font-weight:600;color:#9CA3AF;letter-spacing:0.08em;text-transform:uppercase">F1 Score</th>
            </tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>""", unsafe_allow_html=True)

    with col_r:
        api_ok = bool(os.environ.get("ANTHROPIC_API_KEY"))
        st.markdown("""<div style="font-size:11px;font-weight:600;color:#9CA3AF;letter-spacing:0.1em;
                        text-transform:uppercase;margin-bottom:14px">System Configuration</div>""",
                    unsafe_allow_html=True)
        config = [
            ("Rule Engine","Active ✓"),("NCCI Edit Pairs","675,271"),
            ("AI Model","Claude Sonnet 4.6"),("AI Layer","Active ✓" if api_ok else "⚠ Set API Key"),
            ("Fee Schedule","CMS 2026"),("Training Claims","1,300"),
            ("Fraud Types","9 categories"),("Input Formats","EDI 837P · PDF"),
        ]
        rows2 = "".join([
            f'<tr style="background:{"#F9FAFB" if i%2==0 else "white"}">'
            f'<td style="padding:9px 16px;font-size:12px;color:#9CA3AF;border-bottom:1px solid #F3F4F6;white-space:nowrap;width:140px">{lbl}</td>'
            f'<td style="padding:9px 16px;font-size:12px;font-weight:500;color:#1B2B45;border-bottom:1px solid #F3F4F6">{val}</td></tr>'
            for i,(lbl,val) in enumerate(config)])
        st.markdown(f"""
        <div style="background:white;border:1px solid #E5E7EB;border-radius:8px;overflow:hidden">
          <table style="width:100%;border-collapse:collapse">
            <tbody>{rows2}</tbody>
          </table>
        </div>""", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    api_key     = os.environ.get("ANTHROPIC_API_KEY")
    rule_engine = load_rule_engine()
    llm_checker = load_llm_checker() if api_key else None
    all_claims  = load_all_claims()

    for key,val in [("view","table"),("filter","All Claims"),
                    ("selected_claim",None),("show_modal",False)]:
        if key not in st.session_state: st.session_state[key] = val

    type_counts = Counter(c.get("fraud_type","Legitimate") for c in all_claims)
    total       = len(all_claims)
    fraud_count = sum(1 for c in all_claims if c.get("fraud_indicator"))
    fraud_exp   = sum(c.get("total_charge",0) for c in all_claims if c.get("fraud_indicator"))
    all_types   = (["All Claims"] +
                   sorted([t for t in type_counts if t != "Legitimate"]) +
                   ["Legitimate"])

    # ── SIDEBAR ───────────────────────────────────────────────────────────────
    with st.sidebar:
        # Brand
        st.markdown(f"""
        <div style="padding:18px 16px 16px;border-bottom:1px solid #243650">
          <div style="display:flex;align-items:center;gap:10px">
            <div style="width:32px;height:32px;background:#2563EB;border-radius:7px;
                        display:flex;align-items:center;justify-content:center;
                        font-size:12px;font-weight:700;color:white;font-family:'DM Mono',monospace;
                        flex-shrink:0">CG</div>
            <div>
              <div style="font-size:14px;font-weight:600;color:white">ClaimGuard</div>
              <div style="font-size:10px;color:#4B5563;letter-spacing:0.06em;text-transform:uppercase">Fraud Detection</div>
            </div>
          </div>
        </div>""", unsafe_allow_html=True)

        # Stats
        st.markdown(f"""
        <div style="padding:14px 16px;border-bottom:1px solid #243650">
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px 16px">
            <div>
              <div style="font-size:10px;font-weight:600;color:#94A3B8;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:4px">Claims</div>
              <div style="font-size:22px;font-weight:300;color:white;font-family:'DM Mono',monospace;line-height:1">{total:,}</div>
            </div>
            <div>
              <div style="font-size:10px;font-weight:600;color:#94A3B8;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:4px">Fraud</div>
              <div style="font-size:22px;font-weight:300;color:#F87171;font-family:'DM Mono',monospace;line-height:1">{fraud_count:,}</div>
            </div>
            <div>
              <div style="font-size:10px;font-weight:600;color:#94A3B8;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:3px">Exposure</div>
              <div style="font-size:13px;font-weight:500;color:#F87171;font-family:'DM Mono',monospace">${fraud_exp:,.0f}</div>
            </div>
            <div>
              <div style="font-size:10px;font-weight:600;color:#94A3B8;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:3px">F1 Score</div>
              <div style="font-size:13px;font-weight:500;color:#4ADE80;font-family:'DM Mono',monospace">0.763</div>
            </div>
          </div>
        </div>""", unsafe_allow_html=True)

        # Filter label
        st.markdown("""
        <div style="padding:12px 16px 4px">
          <div style="font-size:9px;font-weight:600;color:#4B5563;letter-spacing:0.1em;text-transform:uppercase">Filter by Type</div>
        </div>""", unsafe_allow_html=True)

        # Nav items — SINGLE button per type, styled as nav
        dot_colors = {
            "All Claims":"#6B7280","Legitimate":"#4ADE80",
            "Upcoding":"#F87171","Code Padding":"#F87171","Phantom Billing":"#F87171",
            "Diagnosis Mismatch":"#FCD34D","Code Substitution":"#FCD34D","Modifier Abuse (-59)":"#FCD34D",
            "Duplicate Billing":"#60A5FA","Unbundling":"#60A5FA","Screening Code Abuse":"#60A5FA",
        }
        for ft in all_types:
            cnt = total if ft=="All Claims" else type_counts.get(ft,0)
            is_active = (ft==st.session_state.filter and st.session_state.view=="table")
            dot = dot_colors.get(ft,"#6B7280")
            # Label: dot + name + count, all in one button
            nav_label = f"● {ft}   {cnt}" if is_active else f"· {ft}   {cnt}"
            if st.button(nav_label, key=f"nav_{ft}", use_container_width=True):
                st.session_state.filter = ft
                st.session_state.view = "table"
                st.rerun()

        # Actions separator
        st.markdown("""<div style="border-top:1px solid #243650;margin:8px 16px 6px"></div>""",
                    unsafe_allow_html=True)

        if st.button("＋  Submit New Claim", key="nav_submit", use_container_width=True):
            st.session_state.view = "submit"; st.rerun()
        if st.button("◎  System Performance", key="nav_perf", use_container_width=True):
            st.session_state.view = "performance"; st.rerun()

        st.markdown("""
        <div style="padding:12px 16px;border-top:1px solid #243650;margin-top:auto">
          <div style="display:flex;align-items:center;gap:10px">
            <div style="width:30px;height:30px;border-radius:50%;background:#2563EB;
                        display:flex;align-items:center;justify-content:center;
                        font-size:11px;font-weight:600;color:white;flex-shrink:0">SP</div>
            <div style="flex:1;min-width:0">
              <div style="font-size:12px;font-weight:500;color:white;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">Shravani Poman</div>
              <div style="font-size:10px;color:#94A3B8;margin-top:1px">Fraud Analyst</div>
            </div>
            <div style="width:7px;height:7px;border-radius:50%;background:#4ADE80;flex-shrink:0"></div>
          </div>
        </div>""", unsafe_allow_html=True)

    # ── MODAL (claim detail popup) ─────────────────────────────────────────────
    if st.session_state.show_modal and st.session_state.selected_claim:
        c = st.session_state.selected_claim
        cid = c.get("claim_id","")

        @st.dialog(f"Claim Detail — {cid}", width="large")
        def show_claim_dialog():
            render_claim_modal(c, rule_engine, llm_checker)
            if st.button("Close", key="close_modal"):
                st.session_state.show_modal = False
                st.session_state.selected_claim = None
                st.rerun()

        show_claim_dialog()

    # ── MAIN CONTENT ──────────────────────────────────────────────────────────
    if st.session_state.view == "table":
        # ── Metric cards row ──────────────────────────────────────────────
        fraud_pct = round(fraud_count / total * 100) if total else 0
        st.markdown(f"""
        <div class="cg-metric-row">
          <div class="cg-metric-card">
            <div class="cg-metric-label">Total Claims</div>
            <div class="cg-metric-value">{total:,}</div>
            <div class="cg-metric-chip blue">Batch evaluation complete</div>
          </div>
          <div class="cg-metric-card">
            <div class="cg-metric-label">Flagged</div>
            <div class="cg-metric-value red">{fraud_count:,}</div>
            <div class="cg-metric-chip red">▲ {fraud_pct}% of claims</div>
          </div>
          <div class="cg-metric-card">
            <div class="cg-metric-label">Fraud Exposure</div>
            <div class="cg-metric-value red" style="font-size:20px">${fraud_exp:,.0f}</div>
            <div class="cg-metric-chip red">Total across flagged claims</div>
          </div>
          <div class="cg-metric-card">
            <div class="cg-metric-label">F1 Score</div>
            <div class="cg-metric-value teal">0.763</div>
            <div class="cg-metric-chip green">Precision 88.8%</div>
          </div>
        </div>""", unsafe_allow_html=True)

        # ── Toolbar: search + submit button ───────────────────────────────
        st.markdown("""
        <div style="background:white;border-bottom:1px solid #E5E7EB;padding:10px 24px;
                    display:flex;align-items:center;gap:12px">
          <svg style="position:absolute;margin-left:10px;width:15px;height:15px;color:#9CA3AF;z-index:1;pointer-events:none"
               viewBox="0 0 15 15" fill="none" xmlns="http://www.w3.org/2000/svg">
            <circle cx="6.5" cy="6.5" r="5" stroke="#9CA3AF" stroke-width="1.3"/>
            <path d="M10.5 10.5L13 13" stroke="#9CA3AF" stroke-width="1.3" stroke-linecap="round"/>
          </svg>
        </div>""", unsafe_allow_html=True)
        search_col, btn1 = st.columns([4, 1])
        with search_col:
            search = st.text_input("search",
                placeholder="  Search by claim ID, patient, provider, or billing code...",
                label_visibility="collapsed", key="search_q")
        with btn1:
            if st.button("＋ Submit Claim", key="top_submit", use_container_width=True):
                st.session_state.view = "submit"; st.rerun()

        render_claims_table(all_claims, search, st.session_state.filter,
                            rule_engine, llm_checker)

    elif st.session_state.view == "submit":
        render_submit(rule_engine, llm_checker, all_claims)

    elif st.session_state.view == "performance":
        render_performance()


if __name__ == "__main__":
    main()
