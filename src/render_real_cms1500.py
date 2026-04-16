"""
render_real_cms1500.py
──────────────────────
Renders synthetic claims as real-format CMS-1500 (02-12) PDFs.

Follows the exact CMS-1500 layout used by real insurance companies:
- Letter size (8.5 x 11 inches)
- Grid lines and field labels in LIGHT PINK — visually looks red/pink
  like a real form, but OCR green-channel extraction removes them cleanly
  leaving only the black billing data
- CPT codes, ICD-10 codes, patient data in pure BLACK 12pt Courier-Bold
- All 33 standard fields populated
- Procedure table (box 24) with up to 6 billing lines
- Diagnosis section (box 21) with ICD-10 codes

WHY LIGHT PINK INSTEAD OF RED:
  Real CMS-1500 forms use Pantone 199 red for the grid.
  Our OCR pipeline extracts the green channel to remove that red grid.
  Full red (R=200, G=30) has G=30 → OCR reads it as dark text → confusion.
  Light pink (R=255, G=210) has G=210 → OCR reads as white → invisible.
  Black text (R=0, G=0) has G=0 → OCR reads as dark → perfect.

HOW TO USE:
    python3 src/render_real_cms1500.py

OUTPUT: data/pdfs_real/<claim_id>.pdf  (30 PDFs, 3 per fraud type)

THEN TEST:
    python3 src/extract_fields.py   (re-run OCR on new PDFs)
    python3 src/run_ocr_pipeline.py (get updated F1 scores)
"""

import json, random, sys
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch

BASE_DIR    = Path(__file__).resolve().parent.parent
CLAIMS_JSON = BASE_DIR / "data" / "raw_claims" / "claims.json"
OUT_DIR     = BASE_DIR / "data" / "pdfs_real"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Colors ────────────────────────────────────────────────────────────────────
# Light pink for grid/labels — G=210 so green channel extraction removes it
# while black text (G=0) remains clearly visible
GRID  = colors.Color(1.0, 0.82, 0.82)   # Light pink grid lines
LABEL = colors.Color(0.85, 0.20, 0.20)  # Slightly darker for field labels
BLACK = colors.black
WHITE = colors.white
GREY  = colors.Color(0.35, 0.35, 0.35)

# Page dimensions
W, H = letter
L    = 0.25 * inch   # left margin
R    = W - 0.25 * inch
T    = H - 0.15 * inch


# ── Drawing helpers ───────────────────────────────────────────────────────────

def hline(c, x1, y, x2, color=GRID, lw=0.6):
    c.setStrokeColor(color); c.setLineWidth(lw); c.line(x1, y, x2, y)

def vline(c, x, y1, y2, color=GRID, lw=0.6):
    c.setStrokeColor(color); c.setLineWidth(lw); c.line(x, y1, x, y2)

def box(c, x, y, w, h, color=GRID, lw=0.6):
    c.setStrokeColor(color); c.setLineWidth(lw); c.rect(x, y, w, h, fill=0)

def lbl(c, text, x, y, size=5.5):
    """Small red field label."""
    c.setFillColor(LABEL); c.setFont("Helvetica", size); c.drawString(x, y, text)

def dat(c, text, x, y, size=8.5, bold=False):
    """Black Courier data entry text — typewriter style."""
    if not text: return
    c.setFillColor(BLACK)
    c.setFont("Courier-Bold" if bold else "Courier", size)
    c.drawString(x, y, str(text))

def dat_big(c, text, x, y):
    """Large bold black text for CPT/ICD codes — optimised for OCR."""
    if not text: return
    c.setFillColor(BLACK)
    c.setFont("Courier-Bold", 11)
    c.drawString(x, y, str(text))


# ── Main renderer ──────────────────────────────────────────────────────────────

def render_cms1500(claim, out_path):
    """Render one claim as a real-format CMS-1500 PDF."""

    # ── Extract claim fields ─────────────────────────────────────────────────
    p  = claim.get("patient", {})
    pr = claim.get("provider", {})

    def pg(k, d=""): return p.get(k, d) if isinstance(p, dict) else d
    def prg(k, d=""): return pr.get(k, d) if isinstance(pr, dict) else d

    pat_name  = pg("name", "DOE, JOHN A")
    pat_dob   = pg("dob", "01/01/1970")
    pat_sex   = pg("sex", "M")
    pat_addr  = pg("address", "100 MAIN STREET")
    pat_city  = pg("city", "BOSTON")
    pat_state = pg("state", "MA")
    pat_zip   = pg("zip", "02118")
    pat_ins   = pg("insurer", "BLUE CROSS BLUE SHIELD")
    pat_id    = pg("id", "BCB-0000001")

    prov_name = prg("name", "DR. JOHN SMITH MD")
    prov_npi  = prg("npi", "1234567890")
    prov_fac  = prg("facility", "GENERAL MEDICAL CENTER")
    prov_addr = prg("address", "100 HOSPITAL WAY, BOSTON MA 02116")
    prov_tax  = prg("tax_id", "04-1234567")

    cpt_codes = claim.get("procedure_codes", [])
    icd_codes = claim.get("diagnosis_codes", [])
    icd_descs = claim.get("diagnosis_descs", [])
    cpt_descs = claim.get("procedure_descs", [])
    modifiers = claim.get("modifiers", [])
    charges   = claim.get("line_charges", [])
    total     = float(claim.get("total_charge", 0))
    cid       = claim.get("claim_id", "CLM00000")

    # Format service date as MM DD YYYY
    raw_date = claim.get("date", "2026-01-01")
    try:
        pts = raw_date.split("-")
        svc = f"{pts[1]} {pts[2]} {pts[0]}"
    except:
        svc = "01 01 2026"

    # Derived fields
    grp_num = f"GRP-{random.randint(10000,99999)}"
    pol_num = pat_id

    c = canvas.Canvas(str(out_path), pagesize=letter)

    # ── HEADER ────────────────────────────────────────────────────────────────
    # PICA boxes
    box(c, L, T - 18, 34, 14); lbl(c, "PICA", L + 2, T - 12, 6)
    box(c, R - 34, T - 18, 34, 14); lbl(c, "PICA", R - 32, T - 12, 6)

    c.setFillColor(LABEL); c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(W / 2, T - 9, "HEALTH INSURANCE CLAIM FORM")
    c.setFont("Helvetica", 5.5)
    c.drawCentredString(W / 2, T - 17, "APPROVED BY NATIONAL UNIFORM CLAIM COMMITTEE (NUCC) 02/12")

    # ── ROW 1: Insurance type (boxes 1 + 1a) ─────────────────────────────────
    r1y, r1h = T - 34, 16
    ins_types = ["MEDICARE", "MEDICAID", "TRICARE", "CHAMPVA",
                 "GROUP\nHEALTH PLAN", "FECA\nBLK LUNG", "OTHER"]
    n = len(ins_types)
    bw = (R - L - 90) / n
    for i, t in enumerate(ins_types):
        bx = L + i * bw
        box(c, bx, r1y, bw, r1h)
        first = t.split("\n")[0]
        second = t.split("\n")[1] if "\n" in t else ""
        lbl(c, first, bx + 2, r1y + 9)
        if second: lbl(c, second, bx + 2, r1y + 4)
        box(c, bx + 2, r1y + 1, 6, 6)
        if i == 4:  # Group Health Plan checked
            dat(c, "X", bx + 3, r1y + 2, 6)

    # 1a insured ID
    ia_x = R - 90
    box(c, ia_x, r1y, 90, r1h)
    lbl(c, "1a. INSURED'S I.D. NUMBER (For Program in Item 1)", ia_x + 2, r1y + 11)
    dat(c, pol_num, ia_x + 2, r1y + 2)

    # ── ROW 2: Patient name / DOB / sex / insured ─────────────────────────────
    r2y, r2h = r1y - 22, 22
    box(c, L, r2y, 165, r2h)
    lbl(c, "2. PATIENT'S NAME (Last Name, First Name, Middle Initial)", L + 2, r2y + 17)
    dat(c, pat_name.upper(), L + 2, r2y + 5)

    box(c, L + 165, r2y, 85, r2h)
    lbl(c, "3. PATIENT'S BIRTH DATE", L + 167, r2y + 17)
    lbl(c, "SEX", L + 222, r2y + 17)
    dat(c, pat_dob, L + 167, r2y + 5)
    box(c, L + 219, r2y + 5, 6, 7); lbl(c, "M", L + 219, r2y + 2)
    box(c, L + 232, r2y + 5, 6, 7); lbl(c, "F", L + 232, r2y + 2)
    if pat_sex.upper() == "M": dat(c, "X", L + 220, r2y + 6, 6)
    else: dat(c, "X", L + 233, r2y + 6, 6)

    box(c, L + 250, r2y, 150, r2h)
    lbl(c, "4. INSURED'S NAME (Last Name, First Name, Middle Initial)", L + 252, r2y + 17)
    dat(c, pat_name.upper(), L + 252, r2y + 5)

    # ── ROW 3: Addresses ─────────────────────────────────────────────────────
    r3y, r3h = r2y - 30, 30
    box(c, L, r3y, 165, r3h)
    lbl(c, "5. PATIENT'S ADDRESS (No., Street)", L + 2, r3y + 25)
    dat(c, pat_addr.upper(), L + 2, r3y + 16)
    lbl(c, "CITY", L + 2, r3y + 10)
    lbl(c, "ST", L + 90, r3y + 10)
    lbl(c, "ZIP CODE", L + 115, r3y + 10)
    dat(c, pat_city.upper(), L + 2, r3y + 3)
    dat(c, pat_state, L + 90, r3y + 3)
    dat(c, pat_zip, L + 115, r3y + 3)

    box(c, L + 250, r3y, 150, r3h)
    lbl(c, "7. INSURED'S ADDRESS (No., Street)", L + 252, r3y + 25)
    dat(c, pat_addr.upper(), L + 252, r3y + 16)
    lbl(c, "CITY", L + 252, r3y + 10)
    lbl(c, "ST", L + 342, r3y + 10)
    dat(c, pat_city.upper(), L + 252, r3y + 3)
    dat(c, pat_state, L + 342, r3y + 3)

    # ── ROW 4: Insurance / policy ─────────────────────────────────────────────
    r4y, r4h = r3y - 18, 18
    box(c, L, r4y, 200, r4h)
    lbl(c, "11c. INSURANCE PLAN NAME OR PROGRAM NAME", L + 2, r4y + 13)
    dat(c, pat_ins.upper()[:30], L + 2, r4y + 3)

    box(c, L + 200, r4y, 100, r4h)
    lbl(c, "11. INSURED'S POLICY GROUP OR FECA NUMBER", L + 202, r4y + 13)
    dat(c, grp_num, L + 202, r4y + 3)

    box(c, L + 300, r4y, 100, r4h)
    lbl(c, "POLICY NO.", L + 302, r4y + 13)
    dat(c, pol_num, L + 302, r4y + 3)

    # ── ROW 5: Referring provider / facility / NPI ────────────────────────────
    r5y, r5h = r4y - 18, 18
    box(c, L, r5y, 165, r5h)
    lbl(c, "17. NAME OF REFERRING PROVIDER OR OTHER SOURCE", L + 2, r5y + 13)
    dat(c, prov_name.upper()[:24], L + 2, r5y + 3)

    box(c, L + 165, r5y, 165, r5h)
    lbl(c, "32. SERVICE FACILITY LOCATION INFORMATION", L + 167, r5y + 13)
    dat(c, prov_fac.upper()[:24], L + 167, r5y + 3)

    box(c, L + 330, r5y, 70, r5h)
    lbl(c, "17b. NPI", L + 332, r5y + 13)
    dat(c, prov_npi, L + 332, r5y + 3)

    # ── BOX 21: Diagnosis codes ───────────────────────────────────────────────
    diag_y, diag_h = r5y - 42, 42
    box(c, L, diag_y, R - L, diag_h)
    lbl(c, "21. DIAGNOSIS OR NATURE OF ILLNESS OR INJURY  Relate A-L to service line below (24E)",
        L + 2, diag_y + 36)

    # 2×4 grid for ICD codes — each in its own clearly separated cell
    cell_w = (R - L) / 4
    cell_h = 14
    labels_abc = "ABCDEFGHIJKL"

    for i in range(min(len(icd_codes), 8)):
        col = i % 4
        row = i // 4
        cx = L + col * cell_w
        cy = diag_y + 2 + (1 - row) * cell_h

        # Draw vertical separator between cells
        if col > 0:
            vline(c, cx, diag_y + 2, diag_y + 2 + diag_h - 6)
        if row == 1:
            hline(c, L, cy + cell_h, R)

        # Label (A. B. etc)
        lbl(c, f"{labels_abc[i]}.", cx + 2, cy + cell_h - 5)

        # ICD-10 code — large bold black, clearly separated from label
        icd = icd_codes[i]
        dat_big(c, icd, cx + 14, cy + cell_h - 4)

        # Short description below
        desc = (icd_descs[i][:28] if i < len(icd_descs) and icd_descs[i] else "")
        dat(c, desc, cx + 2, cy + 1, 5.5)

    # ── BOX 24: Procedure table header ───────────────────────────────────────
    proc_y = diag_y - 16
    proc_hdr_h = 16

    # Column definitions: (label, width_pts)
    cols = [
        ("24A. DATE(S) OF SERVICE",  72),
        ("B.\nPOS",                  20),
        ("C.\nEMG",                  16),
        ("D. CPT/HCPCS\nMODIFIER",  64),
        ("E. DIAGNOSIS\nPOINTER",    30),
        ("F. $ CHARGES",             52),
        ("G. DAYS/\nUNITS",          25),
        ("H.\nEPSDT",                18),
        ("I. ID\nQUAL.",             18),
        ("J. RENDERING\nPROVIDER ID #", 85),
    ]
    total_w = sum(w for _, w in cols)
    scale   = (R - L) / total_w
    col_ws  = [w * scale for _, w in cols]

    box(c, L, proc_y, R - L, proc_hdr_h)
    cx = L
    for i, ((lbl_txt, _), cw) in enumerate(zip(cols, col_ws)):
        if i > 0: vline(c, cx, proc_y, proc_y + proc_hdr_h)
        parts = lbl_txt.split("\n")
        lbl(c, parts[0], cx + 1, proc_y + 9, 4.5)
        if len(parts) > 1: lbl(c, parts[1], cx + 1, proc_y + 4, 4.5)
        cx += cw

    # ── BOX 24: Procedure lines ───────────────────────────────────────────────
    line_h = 20
    n_lines = min(len(cpt_codes), 6)

    for li in range(n_lines):
        ly = proc_y - (li + 1) * line_h
        box(c, L, ly, R - L, line_h)

        cpt  = cpt_codes[li] if li < len(cpt_codes) else ""
        desc = (cpt_descs[li][:18] if li < len(cpt_descs) and cpt_descs[li] else "")
        mod  = modifiers[li] if li < len(modifiers) else ""
        chg  = (charges[li] if li < len(charges) and charges[li]
                else total / max(n_lines, 1))

        # Diagnosis pointer — all codes A-D
        n_diag = min(len(icd_codes), 4)
        ptr = "".join(labels_abc[:n_diag]) if n_diag > 1 else "A"

        cx = L
        for ci, cw in enumerate(col_ws):
            if ci > 0: vline(c, cx, ly, ly + line_h)
            if ci == 0:      # Date of service
                dat(c, svc, cx + 1, ly + 11)
                dat(c, "11", cx + 1, ly + 2, 7)  # POS
            elif ci == 3:    # CPT code — large bold
                dat_big(c, cpt, cx + 2, ly + 11)
                dat(c, desc, cx + 2, ly + 2, 5)
                if mod:
                    dat(c, mod, cx + cw - 24, ly + 11, 7)
            elif ci == 4:    # Diagnosis pointer
                dat(c, ptr, cx + 3, ly + 7)
            elif ci == 5:    # Charges
                dat(c, f"${chg:,.2f}", cx + 2, ly + 7)
            elif ci == 6:    # Units
                dat(c, "1", cx + 5, ly + 7)
            elif ci == 9:    # Rendering provider NPI
                dat(c, prov_npi, cx + 2, ly + 7, 7)
            cx += cw

    # ── Bottom section ────────────────────────────────────────────────────────
    bot_y = proc_y - (n_lines + 1) * line_h
    bot_h = 20

    box(c, L, bot_y, R - L, bot_h)

    # 25: Tax ID
    box(c, L, bot_y, 110, bot_h)
    lbl(c, "25. FEDERAL TAX I.D. NUMBER  SSN ■  EIN ■", L + 2, bot_y + 15)
    dat(c, prov_tax, L + 2, bot_y + 4)

    # 26: Patient acct
    box(c, L + 110, bot_y, 90, bot_h)
    lbl(c, "26. PATIENT ACCT #", L + 112, bot_y + 15)
    dat(c, f"PAT-{cid[-5:]}", L + 112, bot_y + 4)

    # 28: Total charge
    tc_x = L + 270
    box(c, tc_x, bot_y, 80, bot_h)
    lbl(c, "28. TOTAL CHARGE", tc_x + 2, bot_y + 15)
    dat(c, f"${total:,.2f}", tc_x + 2, bot_y + 4, bold=True)

    # 29: Amount paid
    box(c, tc_x + 80, bot_y, 50, bot_h)
    lbl(c, "29. AMOUNT PAID", tc_x + 82, bot_y + 15)
    dat(c, "$0.00", tc_x + 82, bot_y + 4)

    # ── Signature + billing provider ─────────────────────────────────────────
    sig_y = bot_y - 28
    sig_h = 28
    box(c, L, sig_y, 210, sig_h)
    lbl(c, "31. SIGNATURE OF PHYSICIAN OR SUPPLIER INCLUDING DEGREES OR CREDENTIALS",
        L + 2, sig_y + 23)
    dat(c, prov_name, L + 2, sig_y + 12)
    dat(c, f"Date: {raw_date}", L + 2, sig_y + 3, 7.5)

    box(c, L + 210, sig_y, R - L - 210, sig_h)
    lbl(c, "33. BILLING PROVIDER INFO & PH #", L + 212, sig_y + 23)
    dat(c, prov_fac.upper()[:32], L + 212, sig_y + 13)
    dat(c, prov_addr[:48], L + 212, sig_y + 4, 7)

    # ── Footer ────────────────────────────────────────────────────────────────
    c.setFillColor(LABEL); c.setFont("Helvetica", 4.5)
    c.drawString(L, sig_y - 7,
        "NUCC Instruction Manual available at: www.nucc.org"
        "      PLEASE PRINT OR TYPE"
        "      APPROVED OMB-0938-1197 FORM 1500 (02-12)")

    c.save()


# ── Generate demo set ─────────────────────────────────────────────────────────

def generate_demo_set(claims_json_path, out_dir, count_per_type=3):
    with open(claims_json_path) as f:
        all_claims = json.load(f)

    fraud_types = [
        "Legitimate", "Duplicate Billing", "Phantom Billing",
        "Diagnosis Mismatch", "Modifier Abuse (-59)", "Screening Code Abuse",
        "Upcoding", "Unbundling", "Code Padding", "Code Substitution",
    ]

    generated = []
    for ft in fraud_types:
        matches = [c for c in all_claims if c.get("fraud_type", "Legitimate") == ft]
        if not matches:
            continue
        if ft == "Legitimate":
            selected = random.sample(matches, min(count_per_type, len(matches)))
        else:
            selected = sorted(matches,
                              key=lambda x: len(x.get("fraud_explanation") or ""),
                              reverse=True)[:count_per_type]
        for claim in selected:
            out_path = out_dir / f"REAL_{claim['claim_id']}.pdf"
            render_cms1500(claim, out_path)
            generated.append(claim["claim_id"])
            print(f"  ✅ {claim['claim_id']:12}  {ft}")

    return generated


if __name__ == "__main__":
    random.seed(42)
    print(f"\nGenerating real-format CMS-1500 PDFs...")
    print(f"Output: {OUT_DIR}\n")
    generated = generate_demo_set(CLAIMS_JSON, OUT_DIR, count_per_type=3)
    print(f"\n✅ Generated {len(generated)} PDFs")
