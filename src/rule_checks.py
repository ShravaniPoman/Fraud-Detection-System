"""
rule_checks.py
──────────────
Step 4A of the Fraud Detection Pipeline — Rule-Based Fraud Checks.

FRAUD TYPES DETECTED:
    1. Duplicate Billing     — same CPT code billed twice for same visit
    2. Unbundling            — component codes billed separately instead
                               of one comprehensive code (uses NCCI table)
    3. Modifier Abuse (-59)  — modifier -59 used on NCCI-bundled code pair
    4. Screening Code Abuse  — screening CPT billed without required ICD-10
    5. Code Substitution     — non-covered code swapped for covered equivalent
                               (NEW — deterministic lookup table)
"""

import json
import sys
import time
import openpyxl
from pathlib import Path
from collections import defaultdict

# ── File paths ─────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).resolve().parent.parent
NCCI_PATH   = BASE_DIR / "data" / "ncci_edits.xlsx"
NCCI_CSV    = BASE_DIR / "data" / "ncci_pairs.csv"
PARSED_DIR  = BASE_DIR / "data" / "edi_parsed"
RESULTS_DIR = BASE_DIR / "data" / "rule_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

CMS_CONVERSION_FACTOR = 32.35  # CY 2026

# ── Screening code requirements ────────────────────────────────────────────────
SCREENING_REQUIREMENTS = {
    "G0101": {
        "description": "Cervical/vaginal cancer screening",
        "required_diagnoses": {"Z01.41", "Z12.31", "Z00.00", "Z01.419"},
        "allowed_context": "cervical cancer screening encounter",
    },
    "G0103": {
        "description": "PSA screening",
        "required_diagnoses": {"Z12.5", "Z03.89", "Z00.00"},
        "allowed_context": "prostate cancer screening",
    },
    "G0104": {
        "description": "Colorectal cancer screening — flexible sigmoidoscopy",
        "required_diagnoses": {"Z12.11", "Z12.10", "Z00.00"},
        "allowed_context": "colorectal cancer screening",
    },
    "G0105": {
        "description": "Colorectal cancer screening — colonoscopy (high risk)",
        "required_diagnoses": {"Z12.11", "Z12.10", "Z80.0", "Z83.71"},
        "allowed_context": "high risk colorectal cancer screening",
    },
    "G0121": {
        "description": "Colorectal cancer screening — colonoscopy (not high risk)",
        "required_diagnoses": {"Z12.11", "Z12.10", "Z00.00"},
        "allowed_context": "colorectal cancer screening",
    },
    "77067": {
        "description": "Screening mammography, bilateral",
        "required_diagnoses": {"Z12.31", "Z12.39", "Z12.3", "Z00.00"},
        "allowed_context": "breast cancer screening",
    },
    "80061": {
        "description": "Lipid panel",
        "required_diagnoses": {"Z13.6", "Z13.220", "Z00.00", "E78.5", "E78.00",
                               "E78.01", "I10", "I25.10", "I25.9", "Z82.49"},
        "allowed_context": "cardiovascular screening or known dyslipidemia",
    },
}

# ── Code Substitution patterns ─────────────────────────────────────────────────
# Maps (CPT_code, ICD10_code) → (fraud_type, explanation).
# IMPORTANT: Only include patterns where the combo is UNAMBIGUOUSLY fraud —
# i.e., no legitimate clinical scenario exists for this exact (CPT, ICD) pair.
# Patterns removed if ground truth shows legitimate claims with same combo.
CODE_SUBSTITUTION_PATTERNS = {

    # ── Cervical screening code with clearly unrelated diagnoses ─────────────
    # G0101 requires a cancer screening encounter. These diagnoses have zero
    # clinical pathway to cervical cancer screening — no legitimate use.
    ("G0101", "M54.5"): (
        "Code Substitution",
        "CPT G0101 (cervical/vaginal cancer screening) billed with M54.5 (low back pain) — "
        "cervical cancer screening has no clinical relationship to musculoskeletal pain. "
        "G0101 requires a cancer screening encounter diagnosis (Z12.31, Z00.00)."
    ),
    ("G0101", "I10"): (
        "Code Substitution",
        "CPT G0101 (cervical cancer screening) billed with I10 (essential hypertension) — "
        "cervical screening is not indicated for hypertension. "
        "G0101 requires a cancer screening encounter diagnosis (Z12.31, Z00.00)."
    ),
    ("G0101", "N39.0"): (
        "Code Substitution",
        "CPT G0101 (cervical cancer screening) billed with N39.0 (urinary tract infection) — "
        "cervical screening during a UTI visit has no clinical basis. "
        "G0101 requires a cancer screening encounter diagnosis (Z12.31, Z00.00)."
    ),
    ("G0101", "Z12.31"): None,  # LEGITIMATE — cervical screening with correct dx

    # ── Elective screening CT substituted with chest X-ray code ──────────────
    # 71046 with Z13.6 = non-covered screening CT billed as diagnostic X-ray.
    # 71046 with respiratory dx (J06.9, J18.9) IS legitimate — keep those clean.
    ("71046", "Z13.6"): (
        "Code Substitution",
        "CPT 71046 (chest X-ray, 2 views) billed with Z13.6 (cardiovascular screening encounter) — "
        "an elective screening CT scan substituted with a diagnostic chest X-ray code. "
        "Cardiovascular screening uses 93000/93350, not chest X-ray."
    ),
}

# ── Panel reconstruction rules ────────────────────────────────────────────────
# Detects when panel components are billed separately rather than as a panel.
PANEL_COMPONENTS = {
    "80061": ("Lipid panel",                {"82465", "82470", "83721", "84478"}),
    "80048": ("Basic metabolic panel",      {"82310", "82374", "82435", "82565",
                                             "84132", "84295", "84520", "82947"}),
    "80053": ("Comprehensive metabolic panel", {"82040", "82247", "82248", "82310",
                                                "82374", "82435", "82565", "84155",
                                                "84460", "84450", "84075", "84132",
                                                "84295", "84520", "82947"}),
    "80051": ("Electrolyte panel",          {"82374", "82435", "84132", "84295"}),
    "80069": ("Renal function panel",       {"82040", "82310", "82374", "82435",
                                             "82565", "82947", "84132", "84295",
                                             "84520", "84560"}),
    "80076": ("Hepatic function panel",     {"82040", "82247", "82248", "84155",
                                             "84460", "84450", "84075"}),
    "80055": ("Obstetric panel",            {"86900", "86901", "85025", "86703", "87110"}),
    "85025": ("CBC with differential",      {"85014", "85018", "85041", "85048",
                                             "85049", "85004"}),
    "85027": ("CBC without differential",   {"85014", "85018", "85041", "85048"}),
    "93306": ("Complete echo with Doppler", {"93303", "93304", "93307", "93320",
                                             "93321", "93325"}),
    "93000": ("Complete ECG",               {"93005", "93010"}),
}

def check_panel_unbundling(cpt_codes):
    """Detects when component tests are billed separately instead of as a panel."""
    cpt_set = set(cpt_codes)
    for panel_code, (panel_desc, components) in PANEL_COMPONENTS.items():
        matching = cpt_set & components
        if len(matching) >= 2:
            return {
                "fraud_detected":  True,
                "fraud_type":      "Unbundling",
                "confidence":      "high",
                "rule_triggered":  "panel_component_unbundling",
                "explanation":     (
                    f"Procedures {sorted(matching)} are individual components of the "
                    f"{panel_code} ({panel_desc}) panel. Billing them separately instead "
                    f"of using the panel code is unbundling and inflates the claim."
                ),
                "engine": "Rule Engine",
            }
    return None


# ── Known bundle pairs ─────────────────────────────────────────────────────────
# Expanded from 16 → 80+ pairs. Each entry (comprehensive, component) → explanation.
KNOWN_BUNDLES = {

    # LIPID PANEL (80061) — includes total cholesterol, HDL, LDL, triglycerides
    ("80061", "82465"): "Lipid panel (80061) includes total cholesterol (82465)",
    ("80061", "82470"): "Lipid panel (80061) includes HDL cholesterol (82470)",
    ("80061", "83721"): "Lipid panel (80061) includes LDL cholesterol (83721)",
    ("80061", "84478"): "Lipid panel (80061) includes triglycerides (84478)",

    # BASIC METABOLIC PANEL (80048)
    ("80048", "82310"): "BMP (80048) includes calcium (82310)",
    ("80048", "82374"): "BMP (80048) includes CO2/bicarbonate (82374)",
    ("80048", "82435"): "BMP (80048) includes chloride (82435)",
    ("80048", "82565"): "BMP (80048) includes creatinine (82565)",
    ("80048", "82947"): "BMP (80048) includes glucose (82947)",
    ("80048", "84132"): "BMP (80048) includes potassium (84132)",
    ("80048", "84295"): "BMP (80048) includes sodium (84295)",
    ("80048", "84520"): "BMP (80048) includes BUN (84520)",

    # COMPREHENSIVE METABOLIC PANEL (80053) — all BMP + liver function
    ("80053", "82040"): "CMP (80053) includes albumin (82040)",
    ("80053", "82247"): "CMP (80053) includes total bilirubin (82247)",
    ("80053", "82248"): "CMP (80053) includes direct bilirubin (82248)",
    ("80053", "82310"): "CMP (80053) includes calcium (82310)",
    ("80053", "82374"): "CMP (80053) includes CO2/bicarbonate (82374)",
    ("80053", "82435"): "CMP (80053) includes chloride (82435)",
    ("80053", "82565"): "CMP (80053) includes creatinine (82565)",
    ("80053", "82947"): "CMP (80053) includes glucose (82947)",
    ("80053", "84132"): "CMP (80053) includes potassium (84132)",
    ("80053", "84295"): "CMP (80053) includes sodium (84295)",
    ("80053", "84520"): "CMP (80053) includes BUN (84520)",
    ("80053", "84155"): "CMP (80053) includes total protein (84155)",
    ("80053", "84460"): "CMP (80053) includes ALT/SGPT (84460)",
    ("80053", "84450"): "CMP (80053) includes AST/SGOT (84450)",
    ("80053", "84075"): "CMP (80053) includes alkaline phosphatase (84075)",

    # HEPATIC FUNCTION PANEL (80076)
    ("80076", "82040"): "Hepatic panel (80076) includes albumin (82040)",
    ("80076", "82247"): "Hepatic panel (80076) includes total bilirubin (82247)",
    ("80076", "82248"): "Hepatic panel (80076) includes direct bilirubin (82248)",
    ("80076", "84155"): "Hepatic panel (80076) includes total protein (84155)",
    ("80076", "84460"): "Hepatic panel (80076) includes ALT (84460)",
    ("80076", "84450"): "Hepatic panel (80076) includes AST (84450)",
    ("80076", "84075"): "Hepatic panel (80076) includes ALP (84075)",

    # RENAL FUNCTION PANEL (80069)
    ("80069", "82040"): "Renal panel (80069) includes albumin (82040)",
    ("80069", "82310"): "Renal panel (80069) includes calcium (82310)",
    ("80069", "82374"): "Renal panel (80069) includes CO2 (82374)",
    ("80069", "82435"): "Renal panel (80069) includes chloride (82435)",
    ("80069", "82565"): "Renal panel (80069) includes creatinine (82565)",
    ("80069", "82947"): "Renal panel (80069) includes glucose (82947)",
    ("80069", "84132"): "Renal panel (80069) includes potassium (84132)",
    ("80069", "84295"): "Renal panel (80069) includes sodium (84295)",
    ("80069", "84520"): "Renal panel (80069) includes BUN (84520)",
    ("80069", "84560"): "Renal panel (80069) includes uric acid (84560)",

    # ELECTROLYTE PANEL (80051)
    ("80051", "82374"): "Electrolyte panel (80051) includes CO2 (82374)",
    ("80051", "82435"): "Electrolyte panel (80051) includes chloride (82435)",
    ("80051", "84132"): "Electrolyte panel (80051) includes potassium (84132)",
    ("80051", "84295"): "Electrolyte panel (80051) includes sodium (84295)",

    # CBC (85025 / 85027)
    ("85025", "85014"): "CBC with diff (85025) includes hematocrit (85014)",
    ("85025", "85018"): "CBC with diff (85025) includes hemoglobin (85018)",
    ("85025", "85041"): "CBC with diff (85025) includes RBC count (85041)",
    ("85025", "85048"): "CBC with diff (85025) includes WBC count (85048)",
    ("85025", "85049"): "CBC with diff (85025) includes platelet count (85049)",
    ("85025", "85004"): "CBC with diff (85025) includes automated differential (85004)",
    ("85025", "36415"): "CBC (85025) includes venipuncture (36415)",
    ("85027", "85014"): "CBC without diff (85027) includes hematocrit (85014)",
    ("85027", "85018"): "CBC without diff (85027) includes hemoglobin (85018)",
    ("85027", "85041"): "CBC without diff (85027) includes RBC count (85041)",
    ("85027", "85048"): "CBC without diff (85027) includes WBC count (85048)",
    ("85027", "85049"): "CBC without diff (85027) includes platelet count (85049)",

    # ECHO (93306) — complete transthoracic echo with Doppler and color flow
    ("93306", "93303"): "Complete echo (93306) includes limited echo (93303)",
    ("93306", "93304"): "Complete echo (93306) includes limited follow-up echo (93304)",
    ("93306", "93307"): "Complete echo (93306) includes echo without Doppler (93307)",
    ("93306", "93320"): "Complete echo (93306) includes Doppler (93320)",
    ("93306", "93321"): "Complete echo (93306) includes limited Doppler (93321)",
    ("93306", "93325"): "Complete echo (93306) includes color flow Doppler (93325)",
    ("93306", "93000"): "Complete echo (93306) includes ECG (93000)",
    ("93306", "93005"): "Complete echo (93306) includes ECG tracing (93005)",
    ("93306", "93010"): "Complete echo (93306) includes ECG interpretation (93010)",

    # ECG (93000) — complete = tracing + interpretation
    ("93000", "93005"): "Complete ECG (93000) includes tracing (93005)",
    ("93000", "93010"): "Complete ECG (93000) includes interpretation (93010)",

    # OBSTETRIC PANEL (80055)
    ("80055", "86900"): "Obstetric panel (80055) includes ABO blood typing (86900)",
    ("80055", "86901"): "Obstetric panel (80055) includes Rh typing (86901)",
    ("80055", "85025"): "Obstetric panel (80055) includes CBC (85025)",
    ("80055", "86703"): "Obstetric panel (80055) includes HIV testing (86703)",

    # E&M VISIT LEVELS — cannot bill two levels same day
    ("99213", "99212"): "Cannot bill two E&M levels for same patient same day (99213 + 99212)",
    ("99214", "99213"): "Cannot bill two E&M levels for same patient same day (99214 + 99213)",
    ("99214", "99212"): "Cannot bill two E&M levels for same patient same day (99214 + 99212)",
    ("99215", "99214"): "Cannot bill two E&M levels for same patient same day (99215 + 99214)",
    ("99215", "99213"): "Cannot bill two E&M levels for same patient same day (99215 + 99213)",
    ("99215", "99212"): "Cannot bill two E&M levels for same patient same day (99215 + 99212)",
    ("99205", "99204"): "Cannot bill two new patient E&M levels same day (99205 + 99204)",
    ("99205", "99203"): "Cannot bill two new patient E&M levels same day (99205 + 99203)",
    ("99204", "99203"): "Cannot bill two new patient E&M levels same day (99204 + 99203)",
    ("99204", "99202"): "Cannot bill two new patient E&M levels same day (99204 + 99202)",
    ("99203", "99202"): "Cannot bill two new patient E&M levels same day (99203 + 99202)",

    # SURGICAL — component with global procedure
    ("43239", "43235"): "EGD with biopsy (43239) includes diagnostic EGD (43235)",
    ("29881", "29870"): "Knee arthroscopy with meniscectomy (29881) includes diagnostic arthroscopy (29870)",
    ("93306", "93303"): "Complete echo (93306) includes limited echo (93303)",
}


# ── NCCI table loader ──────────────────────────────────────────────────────────
def load_ncci_table(ncci_path):
    """
    Loads NCCI PTP edit table. Tries ncci_pairs.csv first (fast, works on
    Streamlit Cloud), then xlsx fallback, then empty dict.
    """
    import csv
    ncci_dict = {}

    csv_path = NCCI_CSV
    if csv_path.exists():
        print(f"  Loading NCCI pairs from {csv_path.name}...")
        start = time.time()
        with open(csv_path, newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                c1 = row.get('col1', '').strip()
                c2 = row.get('col2', '').strip()
                mi = row.get('modifier_indicator', '0').strip()
                if c1 and c2:
                    ncci_dict[(c1, c2)] = mi
        elapsed = time.time() - start
        print(f"  ✅ Loaded {len(ncci_dict):,} NCCI pairs from CSV in {elapsed:.1f}s")
        return ncci_dict

    if not Path(ncci_path).exists():
        print(f"  ⚠️  NCCI files not found — using built-in bundles only.")
        return ncci_dict

    print(f"  Loading NCCI edit table from {Path(ncci_path).name}...")
    start = time.time()
    try:
        wb = openpyxl.load_workbook(str(ncci_path), read_only=True)
        ws = wb.active
        count = 0
        for row in ws.iter_rows(values_only=True):
            col1     = row[0]
            col2     = row[1]
            modifier = row[5]
            if not col1 or not col2:
                continue
            if not str(col1)[0].isdigit() and not str(col1)[0].isalpha():
                continue
            col1_str = str(col1).strip().zfill(5)
            col2_str = str(col2).strip().zfill(5)
            mod_str  = str(modifier).strip() if modifier else "9"
            ncci_dict[(col1_str, col2_str)] = mod_str
            ncci_dict[(col2_str, col1_str)] = mod_str
            count += 1
        elapsed = time.time() - start
        print(f"  ✅ Loaded {count:,} NCCI edit pairs in {elapsed:.1f}s")
    except Exception as e:
        print(f"  ⚠️  Could not load NCCI table: {e}")
    return ncci_dict


# ══════════════════════════════════════════════════════════════════════════════
class RuleEngine:
    """
    Rule-based fraud detection engine.
    Checks: Duplicate Billing, Modifier Abuse, Unbundling (panel + NCCI),
            Screening Code Abuse, and Code Substitution (NEW).
    """

    def __init__(self, ncci_path=NCCI_PATH):
        print("\nInitializing Rule Engine...")
        self.ncci = load_ncci_table(ncci_path)
        self.known_bundles = KNOWN_BUNDLES
        print("  Rule engine ready.\n")

    # ── CHECK 1: DUPLICATE BILLING ─────────────────────────────────────────────
    def check_duplicate_billing(self, claim):
        proc_codes = claim.get("procedure_codes", [])
        code_counts = {}
        for code in proc_codes:
            code_counts[code] = code_counts.get(code, 0) + 1
        duplicates = {c: n for c, n in code_counts.items() if n > 1}
        if duplicates:
            dup_list = ", ".join([f"{c} (×{n})" for c, n in duplicates.items()])
            return {
                "fraud_detected": True,
                "fraud_type":     "Duplicate Billing",
                "rule_triggered": "duplicate_cpt_code",
                "explanation":    (
                    f"Procedure code(s) billed more than once on the same claim: {dup_list}. "
                    f"Each procedure should be billed exactly once per visit."
                ),
                "confidence":   "high",
                "flagged_codes": list(duplicates.keys()),
            }
        return None

    # ── CHECK 2: MODIFIER ABUSE (-59) ─────────────────────────────────────────
    def check_modifier_abuse(self, claim):
        proc_codes = claim.get("procedure_codes", [])
        modifiers  = claim.get("modifiers", [])

        if not any(m in ["-59", "59", "XS", "XU", "XE", "XP"] for m in modifiers):
            return None

        from itertools import combinations
        for c1, c2 in combinations(proc_codes, 2):
            # Check both orderings in NCCI table
            mi = self.ncci.get((c1, c2)) or self.ncci.get((c2, c1))
            if mi == "1":
                return {
                    "fraud_detected": True,
                    "fraud_type":     "Modifier Abuse (-59)",
                    "rule_triggered": "ncci_modifier_abuse",
                    "explanation":    (
                        f"Modifier -59 applied to NCCI-bundled code pair CPT {c1} and CPT {c2}. "
                        f"These codes cannot be billed separately — modifier -59 is being used "
                        f"to bypass NCCI bundling rules."
                    ),
                    "confidence": "high",
                    "flagged_codes": [c1, c2],
                }
            # Also check known bundles
            if (c1, c2) in self.known_bundles or (c2, c1) in self.known_bundles:
                reason = self.known_bundles.get((c1, c2)) or self.known_bundles.get((c2, c1))
                return {
                    "fraud_detected": True,
                    "fraud_type":     "Modifier Abuse (-59)",
                    "rule_triggered": "known_bundle_modifier_abuse",
                    "explanation":    (
                        f"Modifier -59 applied to known bundled pair {c1} + {c2}. "
                        f"{reason}. Using -59 to bypass this is modifier abuse."
                    ),
                    "confidence": "high",
                    "flagged_codes": [c1, c2],
                }
        return None

    # ── CHECK 3: UNBUNDLING (NCCI + known bundles) ────────────────────────────
    def check_unbundling(self, claim):
        proc_codes = claim.get("procedure_codes", [])
        modifiers  = claim.get("modifiers", [])
        has_59     = any(m in ["-59", "59", "XS", "XU", "XE", "XP"] for m in modifiers)

        from itertools import combinations
        for c1, c2 in combinations(proc_codes, 2):
            # Known bundles — always flag regardless of modifier
            if (c1, c2) in self.known_bundles:
                reason = self.known_bundles[(c1, c2)]
                return {
                    "fraud_detected": True,
                    "fraud_type":     "Unbundling",
                    "rule_triggered": "known_bundle",
                    "explanation":    (
                        f"CPT {c1} and CPT {c2} are billed separately but represent a "
                        f"bundled service. {reason}. Billing component codes separately "
                        f"instead of using the comprehensive code is unbundling."
                    ),
                    "confidence": "high",
                    "flagged_codes": [c1, c2],
                }
            if (c2, c1) in self.known_bundles:
                reason = self.known_bundles[(c2, c1)]
                return {
                    "fraud_detected": True,
                    "fraud_type":     "Unbundling",
                    "rule_triggered": "known_bundle",
                    "explanation":    (
                        f"CPT {c2} and CPT {c1} are billed separately but represent a "
                        f"bundled service. {reason}. Billing component codes separately is unbundling."
                    ),
                    "confidence": "high",
                    "flagged_codes": [c1, c2],
                }

            # NCCI table — modifier indicator 0 = never allowed together
            mi = self.ncci.get((c1, c2)) or self.ncci.get((c2, c1))
            if mi == "0":
                return {
                    "fraud_detected": True,
                    "fraud_type":     "Unbundling",
                    "rule_triggered": "ncci_bundle_hard",
                    "explanation":    (
                        f"CPT {c1} and CPT {c2} cannot be billed together (NCCI modifier "
                        f"indicator = 0). These codes are mutually exclusive — billing both "
                        f"is a hard NCCI unbundling violation."
                    ),
                    "confidence": "high",
                    "flagged_codes": [c1, c2],
                }

        return None

    # ── CHECK 4: SCREENING CODE ABUSE ─────────────────────────────────────────
    def check_screening_code_abuse(self, claim):
        proc_codes  = claim.get("procedure_codes", [])
        diag_codes  = set(claim.get("diagnosis_codes", []))

        for cpt in proc_codes:
            if cpt not in SCREENING_REQUIREMENTS:
                continue
            req = SCREENING_REQUIREMENTS[cpt]
            allowed = req["required_diagnoses"]
            if not diag_codes.intersection(allowed):
                return {
                    "fraud_detected": True,
                    "fraud_type":     "Screening Code Abuse",
                    "rule_triggered": "screening_diagnosis_mismatch",
                    "explanation":    (
                        f"CPT {cpt} ({req['description']}) requires a {req['allowed_context']} "
                        f"diagnosis code (e.g. {', '.join(sorted(allowed)[:3])}). "
                        f"The claim uses {', '.join(sorted(diag_codes))} which does not qualify "
                        f"as a valid screening encounter — this is screening code abuse."
                    ),
                    "confidence": "high",
                    "flagged_codes": [cpt],
                }
        return None

    # ── CHECK 5: CODE SUBSTITUTION (NEW) ──────────────────────────────────────
    def check_code_substitution(self, claim):
        """
        Detects non-covered procedures disguised as covered codes.
        Uses CODE_SUBSTITUTION_PATTERNS lookup: (CPT, ICD10) → fraud explanation.
        """
        proc_codes = claim.get("procedure_codes", [])
        diag_codes = claim.get("diagnosis_codes", [])

        for cpt in proc_codes:
            for icd in diag_codes:
                result = CODE_SUBSTITUTION_PATTERNS.get((cpt, icd))
                if result is None:
                    continue  # explicitly marked as legitimate
                if result is not None:
                    fraud_type, explanation = result
                    return {
                        "fraud_detected": True,
                        "fraud_type":     fraud_type,
                        "rule_triggered": "code_substitution_pattern",
                        "explanation":    explanation,
                        "confidence":     "high",
                        "flagged_codes":  [cpt],
                    }
        return None

    # ── MAIN CHECK ─────────────────────────────────────────────────────────────
    def check(self, claim):
        """
        Runs all rule checks in order. Returns first fraud found, or clean result.
        Order: Duplicate → Modifier Abuse → Panel Unbundling → NCCI Unbundling
               → Screening Abuse → Code Substitution
        """
        claim_id = claim.get("claim_id", "UNKNOWN")

        for check_fn in [
            self.check_duplicate_billing,
            self.check_modifier_abuse,
            lambda c: check_panel_unbundling(c.get("procedure_codes", [])),
            self.check_unbundling,
            self.check_screening_code_abuse,
            self.check_code_substitution,   # NEW
        ]:
            result = check_fn(claim)
            if result:
                result["claim_id"]   = claim_id
                result["checked_by"] = "rule_engine"
                return result

        return {
            "claim_id":       claim_id,
            "fraud_detected": False,
            "fraud_type":     None,
            "rule_triggered": None,
            "explanation":    (
                "All rule-based checks passed. No duplicate billing, unbundling, "
                "modifier abuse, screening code abuse, or code substitution detected."
            ),
            "confidence":  "high",
            "checked_by":  "rule_engine",
        }


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    engine = RuleEngine()
    claim_files = sorted(PARSED_DIR.glob("*.json"))
    if not claim_files:
        print(f"No parsed claims found in {PARSED_DIR}")
        print("Run src/parse_edi.py first.")
        sys.exit(1)

    results = []
    for cf in claim_files:
        with open(cf) as f:
            claim = json.load(f)
        result = engine.check(claim)
        results.append(result)

    fraud_count = sum(1 for r in results if r["fraud_detected"])
    print(f"\nProcessed {len(results):,} claims")
    print(f"Fraud detected: {fraud_count:,} ({fraud_count/len(results)*100:.1f}%)")
    by_type = defaultdict(int)
    for r in results:
        if r["fraud_detected"]:
            by_type[r["fraud_type"]] += 1
    for ft, cnt in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"  {ft:<30} {cnt:>4}")
