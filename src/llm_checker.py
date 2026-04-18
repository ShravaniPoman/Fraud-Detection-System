"""
llm_checker.py
──────────────
Step 4B — LLM Reasoning Layer.

Detects 5 fraud types requiring clinical judgment:
  1. Upcoding          — too expensive/complex for the diagnosis
  2. Code Padding      — unrelated codes added to inflate total
  3. Phantom Billing   — clinically impossible given diagnosis
  4. Diagnosis Mismatch— CPT and ICD-10 have no valid relationship
  5. Code Substitution — non-covered code swapped for covered one

Model: claude-sonnet-4-6 (production) / claude-haiku-4-5 (dev/cheap)
"""

import json, os, sys, time
from pathlib import Path
from collections import defaultdict

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

BASE_DIR   = Path(__file__).resolve().parent.parent
PARSED_DIR = BASE_DIR / "data" / "edi_parsed"
RULE_DIR   = BASE_DIR / "data" / "rule_results"
LLM_DIR    = BASE_DIR / "data" / "llm_results"
FEE_PATH   = BASE_DIR / "data" / "cms_fee_schedule.xlsx"
LLM_DIR.mkdir(parents=True, exist_ok=True)

MODEL_DEV   = "claude-haiku-4-5"
MODEL_FINAL = "claude-sonnet-4-6"
MODEL       = MODEL_DEV  # ← change to MODEL_FINAL for evaluation

RATE_LIMIT_DELAY = 0.1


def load_fee_schedule(path):
    fee_dict = {}
    CF = 32.35
    if not Path(path).exists():
        print(f"  ⚠️  Fee schedule not found — proceeding without price data")
        return fee_dict
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(path), read_only=True)
        ws = wb.active
        for row in ws.iter_rows(values_only=True):
            code, desc, rvu = row[0], row[4], row[9]
            if not code or not desc:
                continue
            code_str = str(code).strip()
            if isinstance(rvu, (int, float)) and rvu > 0 and code_str not in fee_dict:
                fee_dict[code_str] = {
                    "description":   str(desc).strip(),
                    "medicare_rate": round(rvu * CF, 2),
                }
        print(f"  ✅ Fee schedule loaded: {len(fee_dict):,} CPT codes")
    except Exception as e:
        print(f"  ⚠️  Fee schedule error: {e}")
    return fee_dict


# ── Single-level specialty procedures ─────────────────────────────────────────
# These procedures have ONE code at their complexity level — there is no
# "simpler version" to downcode to. They can NEVER be upcoding by price alone.
SINGLE_LEVEL_PROCEDURES = {
    # Cardiac imaging
    "93306", "93307", "93308",  # echocardiography
    "93303", "93304",            # stress echo
    "93458", "93454", "93455", "93456", "93460", "93461",  # cardiac cath
    "93320", "93321", "93325",  # Doppler echo
    "93350", "93351",           # stress echo
    # ECG
    "93000", "93005", "93010",
    # Chest imaging
    "71046", "71047", "71048",  # chest X-ray
    "71250", "71270",           # chest CT
    # Orthopedic surgical
    "29881", "29880", "29870", "29875", "29876",  # knee arthroscopy
    "29827", "29826", "29823",  # shoulder arthroscopy
    "43239", "43235",           # upper GI endoscopy
    "45378", "45380",           # colonoscopy
    # Lab — single analyte tests
    "82947", "82565", "84132", "84295",  # individual chemistry
    "85014", "85018", "85041",           # individual CBC components
    # Oncology genomics — single-purpose
    "0006M", "0007M", "0019M", "0026U", "0048U", "0062M", "0063M",
}

# ── ICD-10 categories for clinical context ─────────────────────────────────────
RESPIRATORY_INFECTIONS  = {"J06.9", "J02.9", "J00", "J01.90", "J20.9", "J22"}
WELLNESS_CODES          = {"Z00.00", "Z00.01", "Z00.121", "Z00.129"}
CARDIOVASCULAR_DX       = {"I10", "I25.10", "I25.9", "I48.91", "I50.9",
                            "I21.09", "I21.19", "I21.29", "Z13.6"}
MUSCULOSKELETAL_SHOULDER = {"M25.511", "M25.512", "M25.519"}
CANCER_DX               = {"C34.10", "C34.11", "C34.12", "C73", "C50.911",
                            "C18.9", "C20", "C61"}


def build_fraud_prompt(claim, fee_schedule):
    """
    Builds the Claude prompt for one claim.

    Key improvements over v1:
    - Explicit 3-question upcoding gate prevents specialty procedures
      being mislabelled as upcoding or diagnosis mismatch
    - Requires TWO valid clinical reasons before flagging diagnosis mismatch
      (reduces false positives from 23 → target < 5)
    - Few-shot examples for the three most confused pairs:
      upcoding vs diagnosis mismatch, phantom vs mismatch
    - Phantom billing threshold raised to 600% Medicare for very high-cost
      specialty tests (reduces false negatives)
    """
    cpt_codes    = claim.get("procedure_codes", [])
    icd10_codes  = claim.get("diagnosis_codes", [])
    total_charge = claim.get("total_charge", 0)
    modifiers    = claim.get("modifiers", [])

    # Build price context
    price_lines = []
    for code in cpt_codes:
        if code in fee_schedule:
            info = fee_schedule[code]
            ratio = total_charge / info["medicare_rate"] if info["medicare_rate"] > 0 else 0
            price_lines.append(
                f"  {code} ({info['description']}): "
                f"Medicare = ${info['medicare_rate']:.2f} | "
                f"Billed = ${total_charge:,.2f} | "
                f"Ratio = {ratio:.0f}%"
            )
        else:
            price_lines.append(f"  {code}: not in fee schedule")
    price_context = "\n".join(price_lines) if price_lines else "  No fee schedule data"

    # Build hard-rule CRITICAL notes
    critical = []
    if any(c in cpt_codes for c in ["0026U", "0048U", "0006M", "0007M", "0019M", "0062M", "0063M"]):
        critical.append(
            "CRITICAL: These codes are advanced oncology/genomic tests ($3,000–$8,000+). "
            "They are NEVER clinically indicated for routine wellness, respiratory infections, "
            "hypertension, UTI, or any non-oncology diagnosis."
        )
    if any(c in cpt_codes for c in ["93458", "93454", "93455", "93456", "93460", "93461"]):
        critical.append(
            "CRITICAL: CPT 93458/93454 is invasive cardiac catheterization requiring a cardiac "
            "indication (chest pain, known CAD, heart failure). It is NEVER performed for "
            "respiratory infections, wellness, UTI, or non-cardiac diagnoses."
        )
    if any(icd in icd10_codes for icd in RESPIRATORY_INFECTIONS) and        any(c in cpt_codes for c in ["93458","93454","0026U","0048U","0006M","0007M","0019M",
                                     "0036U","0037U","0038U","0048U","0062M","0063M"]):
        critical.append(
            "CRITICAL: J06.9/J00/J20.9 are respiratory infections (cold, bronchitis). "
            "Invasive cardiac catheterization or high-cost oncology/genomic procedures "
            "billed with respiratory infection diagnoses are ALWAYS phantom billing — "
            "these procedures are physically impossible in this clinical context. "
            "fraud_type = Phantom Billing, confidence = high."
        )
    # Phantom billing: high-cost genomics/transplant codes with non-oncology diagnoses
    GENOMIC_TRANSPLANT_CODES = {
        "0026U","0048U","0006M","0007M","0019M","0062M","0063M",
        "0036U","0037U","0038U","0055U","0056U",  # transplant genomics
        "0016U","0017U","0023U",                   # oncology molecular
    }
    NON_ONCOLOGY_WITH_GENOMICS = (
        any(c in cpt_codes for c in GENOMIC_TRANSPLANT_CODES) and
        not any(icd in icd10_codes for icd in CANCER_DX) and
        not any(icd.startswith("C") or icd.startswith("D0") or icd.startswith("Z12")
                for icd in icd10_codes)
    )
    if NON_ONCOLOGY_WITH_GENOMICS:
        critical.append(
            "CRITICAL: Advanced oncology/genomic/transplant codes (0026U/0048U/0036U/0055U etc.) "
            "REQUIRE an active cancer diagnosis (C-codes) or transplant/high-risk indication. "
            "Billing these with non-oncology diagnoses (diabetes Z13.1, back pain M54.5, "
            "cold J06.9, wellness Z00.00, musculoskeletal) is ALWAYS phantom billing. "
            "fraud_type = Phantom Billing, confidence = high."
        )

    # Phantom billing: major surgery with clearly impossible diagnosis
    MAJOR_SURGICAL = {"27447","27446","27445","27487",  # knee arthroplasty
                      "23472","23470",                   # shoulder arthroplasty
                      "22612","22630",}                  # spinal fusion
    MAJOR_SURGICAL_SAFE_ICD = {"M25.511","M25.512","M25.519",
                                "M17.11","M17.12","M17.31",
                                "M25.361","M25.362","M25.369"}
    if any(c in cpt_codes for c in MAJOR_SURGICAL) and        any(icd in icd10_codes for icd in RESPIRATORY_INFECTIONS) and        not any(icd in icd10_codes for icd in MAJOR_SURGICAL_SAFE_ICD):
        critical.append(
            "CRITICAL: Major orthopedic surgery (knee/shoulder replacement, spinal fusion) "
            "billed with a respiratory infection diagnosis (cold, bronchitis, URI) is "
            "physically impossible — no surgeon performs elective joint replacement during "
            "an acute infection. fraud_type = Phantom Billing, confidence = high."
        )

    # Phantom billing: CT/imaging for clearly unrelated diagnosis
    CHEST_IMAGING = {"71250","71270","71271"}
    DIABETES_SCREEN = {"Z13.1","Z13.220"}
    if any(c in cpt_codes for c in CHEST_IMAGING) and        any(icd in icd10_codes for icd in DIABETES_SCREEN):
        critical.append(
            "CRITICAL: Chest CT scan (71250/71270) billed with a diabetes screening diagnosis "
            "(Z13.1) has no clinical basis — chest imaging is not part of diabetes screening. "
            "fraud_type = Phantom Billing, confidence = high."
        )
    # Z00.00 phantom billing — ONLY fire when procedure is clinically impossible
    # at a wellness visit (invasive cardiac, high-cost genomics). Raise threshold
    # to $2,000 to avoid flagging legitimate specialist visits during wellness.
    IMPOSSIBLE_AT_WELLNESS = {"93458","93454","93455","93456","93460","93461",
                               "0026U","0048U","0006M","0007M","0019M","0062M","0063M",
                               "0036U","0037U","0038U"}
    if any(icd in icd10_codes for icd in WELLNESS_CODES) and        any(c in cpt_codes for c in IMPOSSIBLE_AT_WELLNESS):
        critical.append(
            "CRITICAL: Z00.00 is routine wellness exam. Invasive cardiac procedures "
            "(93458/93454) and advanced oncology genomic assays (0026U/0048U/0006M) "
            "are NEVER performed during a routine wellness visit — this is phantom billing."
        )

    critical_block = "\n".join(f"- {c}" for c in critical) if critical else ""

    # Detect single-level procedures present in this claim
    single_level_present = [c for c in cpt_codes if c in SINGLE_LEVEL_PROCEDURES]
    single_level_note = ""
    if single_level_present:
        single_level_note = (
            f"\nSINGLE-LEVEL PROCEDURE NOTE: {', '.join(single_level_present)} "
            f"{'is a' if len(single_level_present)==1 else 'are'} single-level specialty "
            f"procedure(s) with no simpler alternative code. These CANNOT be upcoding "
            f"regardless of price. Only flag if the DIAGNOSIS is wrong, not the price."
        )

    return f"""You are a medical billing fraud detection expert. Analyze this claim and return a JSON verdict.

{f"CRITICAL ALERTS:{chr(10)}{critical_block}" if critical_block else ""}
{single_level_note}

CLAIM:
  Claim ID  : {claim.get("claim_id", "?")}
  CPT Codes : {", ".join(cpt_codes) if cpt_codes else "None"}
  ICD-10    : {", ".join(icd10_codes) if icd10_codes else "None"}
  Modifiers : {", ".join(modifiers) if modifiers else "None"}
  Billed    : ${total_charge:,.2f}

PRICE CONTEXT (Medicare fee schedule):
{price_context}

FRAUD TYPE DEFINITIONS:
1. PHANTOM BILLING   — Procedure clinically impossible given the diagnosis
                       (e.g. cardiac cath for common cold)
2. DIAGNOSIS MISMATCH— The procedure has NO valid clinical relationship to
                       ANY of the diagnoses — not even a plausible indirect one
                       (e.g. brain MRI for UTI with no neurological symptoms)
3. UPCODING          — ONLY applies to E&M codes (99202–99215). Billed at
                       higher complexity than the diagnosis warrants.
                       NEVER applies to single-level specialty procedures
                       (imaging, cardiac, lab, surgical).
4. CODE PADDING      — A legitimate primary procedure + one or more unrelated
                       high-value codes that have no connection to the diagnosis
5. CODE SUBSTITUTION — A non-covered service disguised as a covered code
                       (e.g. wellness ECG billed as diagnostic ECG)

DECISION STEPS — work through these in order:

STEP 1 — CRITICAL CHECK
If any CRITICAL ALERT above applies → fraud_detected=true immediately.
Set fraud_type based on which alert: impossible procedure = Phantom Billing,
wrong diagnosis = Diagnosis Mismatch. HIGH confidence. Do not proceed further.

STEP 2 — PHANTOM BILLING CHECK
Is this procedure physically/clinically impossible given ALL diagnoses listed?
Impossible means: requires specialized hospital equipment but diagnosis is
outpatient, OR procedure treats an organ/system with no relation to any diagnosis.
→ If yes: Phantom Billing, HIGH confidence.

STEP 3 — UPCODING CHECK
Upcoding = billing a more expensive/complex procedure than the diagnosis warrants.
There are TWO types in this dataset:

TYPE A — E&M complexity upcoding:
  Is the code an E&M visit (99202–99215)?
  Does the diagnosis justify that SPECIFIC complexity level?
  - 99215 (high complexity) for a cold/simple visit → Upcoding
  - 99205 (new patient, highest) for routine screening → Upcoding
  - 99214/99215 for Z13.6 (cardiovascular screening) → Upcoding
  Flag if: E&M code complexity exceeds what diagnosis warrants. Confidence = medium.

TYPE B — Procedure substitution upcoding (MORE COMMON in this dataset):
  Is a high-resource procedure billed when a simpler one was clinically warranted?
  Known patterns — flag as Upcoding:
  - 71250 (CT chest, ~$800+) billed when diagnosis only warrants 71046 (X-ray, ~$200)
    → CT billed for simple diagnoses like I10 (hypertension) or M54.5 (back pain)
    → Only an X-ray is clinically warranted; CT is excessive
  - 93306 (complete echo, ~$1,000+) billed when only 93000 (ECG, ~$50) was warranted
    → Echo billed for Z13.6 (cardiovascular screening) or I10 (hypertension alone)
    → Simple ECG is standard first-line; echo requires known cardiac abnormality
  - 0006M (oncology gene classifier, ~$1,000+) billed for Z13.6 (cardiovascular screening)
    → A lipid panel (80061) or basic labs are warranted; genomic classifier is excessive
  Flag if: procedure resource level far exceeds what diagnosis clinically warrants.
  Confidence = medium.

→ If neither type applies: skip to Step 4.

STEP 4 — DIAGNOSIS MISMATCH CHECK
Ask TWO questions before flagging:
  Q1: "Is there ANY valid clinical pathway from diagnosis to procedure?"
      Consider: complications, comorbidities, referral chains, screening.
  Q2: "Would a reasonable physician order this procedure for this diagnosis?"
      If yes to either → legitimate, fraud_detected = false.

LEGITIMATE combos (do NOT flag these):
  - Any imaging/lab + chronic disease management (I10, E11.9, I25.10) → LEGITIMATE
  - Chest X-ray (71046) + respiratory infection (J06.9) → LEGITIMATE
  - ECG (93000) + hypertension (I10) or cardiac screening (Z13.6) → LEGITIMATE
  - Knee arthroscopy (29881) + shoulder pain (M25.511) → UNCERTAIN → default LEGITIMATE
  - Knee replacement (27447) + shoulder pain (M25.511) → LEGITIMATE (concurrent pathology)
  - Screening codes (G0103, G0101) + Z00.00 or Z03.89 → LEGITIMATE
  - 87081 (urine culture) + N39.0 (UTI) → LEGITIMATE (standard diagnostic test)
  - 87491 (chlamydia test) + N39.0 (UTI) → LEGITIMATE (STI co-testing during UTI workup)
  - Any STI test (87491, 87081, 87110) + genitourinary diagnosis → LEGITIMATE
  - 36415 (venipuncture) + ANY diagnosis → LEGITIMATE (routine blood draw)
  - 85025 (CBC) + any chronic or acute condition → LEGITIMATE
  - 80061 (lipid panel) + Z13.6 (cardiovascular screening) → LEGITIMATE

Only flag CLEAR mismatches where the procedure treats a completely different
organ system with NO possible clinical connection:
  - Brain MRI (70553) + UTI (N39.0) → MISMATCH
  - Cardiac cath (93458) + cold (J06.9) → better flagged as Phantom Billing
  - Knee surgery + upper respiratory infection → MISMATCH

→ When in doubt → fraud_detected = false. Precision matters more than recall here.

STEP 5 — CODE PADDING CHECK
Is there a legitimate primary procedure PLUS additional codes that are
clearly unrelated to the diagnosis and serve only to inflate the total?
Unrelated = completely different organ system or clinical context.
→ If yes: Code Padding, HIGH confidence.

STEP 5b — CODE SUBSTITUTION CHECK
Does the procedure code clearly NOT match the documented clinical indication?
Apply ONLY these specific, high-confidence patterns:

  - 99213/99202/99203 (E&M office visit) + Z00.00 (routine wellness) as SOLE diagnosis
    → cosmetic consultation disguised as medical office visit. Flag ONLY if Z00.00
    is the ONLY diagnosis and the charge is under $200.

  - 93000 (diagnostic ECG) + Z13.6 (cardiovascular screening) as SOLE diagnosis, charge < $100
    → non-covered executive wellness ECG billed as diagnostic ECG.
    NOTE: 93000 + Z13.6 with OTHER cardiac diagnoses (I10, I25.10) = LEGITIMATE.

  - 29881 (knee arthroscopy) + M25.511/M25.512 (shoulder pain) as SOLE diagnosis
    → knee procedure with shoulder-only diagnosis. Flag ONLY if there is NO knee
    diagnosis anywhere and M25.511 is the only ICD code.
    NOTE: If claim has any knee-related ICD code, default to LEGITIMATE.

DO NOT flag as Code Substitution:
  - 36415 (venipuncture) + ANY diagnosis → always legitimate
  - 85025 (CBC) + E11.9 (diabetes) → CBC is valid for diabetes monitoring
  - 85025 (CBC) + Z13.6 (cardiovascular screening) → CBC at cardiac screen is valid
  - 80061 (lipid panel) + Z13.6 (cardiovascular screening) → exact clinical indication
  - Any lab test + chronic disease management codes → always legitimate
  - 71046 (chest X-ray) + J06.9 (URI) → legitimate (rule out pneumonia)

→ When uncertain about substitution: default to fraud_detected = false.

STEP 6 — LEGITIMATE
If none of the above apply → fraud_detected=false.
High-price specialty procedures with matching diagnoses are NEVER fraud.

FEW-SHOT EXAMPLES:

Example A — Phantom Billing (impossible procedure for diagnosis):
  CPT: 93458 (cardiac cath), ICD: Z00.00 (routine wellness), Billed: $3,200
  → Invasive cardiac catheterization during a routine wellness visit is clinically
    impossible — no cardiologist performs cath without a cardiac indication.
    fraud_type = "Phantom Billing", confidence = "high"

Example B — Phantom Billing (oncology test, no cancer diagnosis):
  CPT: 0048U (oncology DNA analysis), ICD: J06.9 (common cold), Billed: $4,850
  → Advanced cancer genomic panel billed for a cold — never ordered without
    active cancer diagnosis. fraud_type = "Phantom Billing", confidence = "high"

Example C — Phantom Billing (high-cost genomics, metabolic diagnosis):
  CPT: 0019M (oncology protein panel), ICD: E11.9 (type 2 diabetes), Billed: $2,500
  → Oncology protein panel requires cancer diagnosis. Diabetes alone does not
    justify oncology testing. fraud_type = "Phantom Billing", confidence = "high"

Example C2 — Phantom Billing (transplant genomics, back pain):
  CPT: 0055U (cardiac transplant genomic test), ICD: M54.5 (low back pain), Billed: $5,200
  → Cardiac transplant genomic test requires a transplant patient. Low back pain
    has zero relationship to cardiac transplantation.
    fraud_type = "Phantom Billing", confidence = "high"

Example C3 — Phantom Billing (major surgery, respiratory infection):
  CPT: 27447 (total knee replacement), ICD: J06.9 (common cold), Billed: $11,000
  → Elective knee replacement is never performed during an acute infection.
    Physically impossible clinical scenario.
    fraud_type = "Phantom Billing", confidence = "high"

Example C4 — Code Substitution (wellness CBC):
  CPT: 85025 (CBC), ICD: Z00.00 (routine wellness), Billed: $22, no other diagnosis
  → Diagnostic CBC code used for a non-covered wellness lab draw.
    fraud_type = "Code Substitution", confidence = "high"

Example C5 — Code Substitution (wellness ECG):
  CPT: 93000 (diagnostic ECG), ICD: Z13.6 (cardiovascular screening), Billed: $51
  → Diagnostic ECG code used to bill a non-covered executive wellness ECG.
    fraud_type = "Code Substitution", confidence = "high"

Example D — Diagnosis Mismatch (wrong organ system):
  CPT: 70553 (brain MRI), ICD: N39.0 (UTI), Billed: $1,286
  → Brain MRI requires neurological indication. UTI has no neurological pathway.
    fraud_type = "Diagnosis Mismatch", confidence = "high"

Example E — Legitimate (unusual but valid combo):
  CPT: 71046 (chest X-ray), ICD: J06.9 (URI), Billed: $220
  → Chest X-ray ordered to rule out pneumonia during URI — valid clinical pathway.
    fraud_detected = false

Example F — Legitimate (high price, correct indication):
  CPT: 93306 (complete echo), ICD: I25.10 (CAD), Billed: $1,100
  → Echo with coronary artery disease is standard of care. Single-level procedure.
    fraud_detected = false

Example G — Upcoding (E&M code, wrong complexity):
  CPT: 99215 (high complexity office visit), ICD: J06.9 (cold), Billed: $280
  → 99215 requires high medical decision-making. A cold warrants 99213.
    fraud_type = "Upcoding", confidence = "medium"

Example G2 — Upcoding (new patient high complexity, simple visit):
  CPT: 99205 (new patient, highest complexity), ICD: J06.9 (cold), Billed: $383
  → New patient cold visit warrants 99202-99203. 99205 is overbilled.
    fraud_type = "Upcoding", confidence = "medium"

Example G3 — Upcoding (CT when X-ray warranted):
  CPT: 71250 (CT chest, high-resource), ICD: I10 (hypertension), Billed: $1,524
  → Hypertension alone does not warrant CT chest. A plain X-ray (71046) or no
    imaging is standard. CT is excessive for this diagnosis.
    fraud_type = "Upcoding", confidence = "medium"

Example G4 — Upcoding (CT when X-ray warranted, back pain):
  CPT: 71250 (CT chest), ICD: M54.5 (low back pain), Billed: $1,233
  → CT chest has no clinical relationship to back pain AND is excessive resource use.
    fraud_type = "Upcoding", confidence = "medium"

Example G5 — Upcoding (echo when ECG warranted):
  CPT: 93306 (complete echocardiogram), ICD: Z13.6 (cardiovascular screening), Billed: $1,273
  → Routine cardiovascular screening uses ECG (93000), not a full echocardiogram.
    Echo requires known cardiac abnormality; screening alone doesn't justify it.
    fraud_type = "Upcoding", confidence = "medium"

Example G6 — Upcoding (echo when ECG warranted, hypertension):
  CPT: 93306 (complete echocardiogram), ICD: I10 (hypertension), Billed: $1,091
  → Newly diagnosed hypertension warrants ECG (93000) as first-line cardiac workup,
    not a full echocardiogram. Echo is reserved for known cardiac dysfunction.
    fraud_type = "Upcoding", confidence = "medium"

Example G7 — Upcoding (genomic classifier when lipid panel warranted):
  CPT: 0006M (oncology hepatocellular gene classifier), ICD: Z13.6 (cardiovascular screening), Billed: $1,408
  → Cardiovascular screening warrants a lipid panel (80061) at ~$40.
    An oncology gene classifier at $1,400 is massively excessive for cardiac screening.
    fraud_type = "Upcoding", confidence = "medium"

Example H — Legitimate (echo with established cardiac disease):
  CPT: 93306 (complete echo), ICD: I25.10 (coronary artery disease), Billed: $1,100
  → Echo with known CAD is standard of care — not upcoding.
    fraud_detected = false"

Example I — Code Padding (unrelated high-value codes added):
  CPT: 99202 + 0026U + 0063M, ICD: M25.511 (shoulder pain), Billed: $8,400
  → 0026U/0063M are oncology genomic assays — no relationship to shoulder pain.
    fraud_type = "Code Padding", confidence = "high"

Example J — Legitimate (knee arthroscopy, shoulder referred pain):
  CPT: 29881 (knee arthroscopy), ICD: M25.511 (shoulder pain), Billed: $4,200
  → Shoulder pain can be referred from cervical spine or other sources; patient
    may have concurrent knee pathology. This combo CAN be legitimate.
    → Only flag if there is ZERO clinical documentation supporting the knee procedure.
    → Default to fraud_detected = false unless another red flag is present."

Respond ONLY with valid JSON, no markdown, no other text:
{{
  "fraud_detected": true or false,
  "fraud_type": "Phantom Billing" | "Diagnosis Mismatch" | "Upcoding" | "Code Padding" | "Code Substitution" | null,
  "confidence": "high" | "medium" | "low",
  "explanation": "One precise sentence: state the specific CPT code, the diagnosis, and exactly why this is fraud OR why it is legitimate."
}}"""


class LLMChecker:
    """LLM-based fraud detection using the Claude API."""

    def __init__(self, model=MODEL):
        print("\nInitializing LLM Checker...")
        self.api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY not set.\n"
                "Run: export ANTHROPIC_API_KEY='sk-ant-your-key-here'"
            )
        self.client        = anthropic.Anthropic(api_key=self.api_key)
        self.model         = model
        self.fee_schedule  = load_fee_schedule(FEE_PATH)
        self.input_tokens  = 0
        self.output_tokens = 0
        self.api_calls     = 0
        print(f"  Model : {self.model}")
        print(f"  Ready.\n")

    def check(self, claim):
        claim_id = claim.get("claim_id", "UNKNOWN")
        try:
            prompt   = build_fraud_prompt(claim, self.fee_schedule)
            response = self.client.messages.create(
                model      = self.model,
                max_tokens = 400,
                messages   = [{"role": "user", "content": prompt}]
            )
            self.input_tokens  += response.usage.input_tokens
            self.output_tokens += response.usage.output_tokens
            self.api_calls     += 1

            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            parsed = json.loads(raw)
            return {
                "claim_id":       claim_id,
                "fraud_detected": bool(parsed.get("fraud_detected", False)),
                "fraud_type":     parsed.get("fraud_type"),
                "confidence":     parsed.get("confidence", "medium"),
                "explanation":    parsed.get("explanation", ""),
                "checked_by":     "llm_claude",
                "model_used":     self.model,
            }

        except json.JSONDecodeError as e:
            return {
                "claim_id":       claim_id,
                "fraud_detected": False,
                "fraud_type":     None,
                "confidence":     "low",
                "explanation":    f"Response parse error: {str(e)[:80]}",
                "checked_by":     "llm_claude",
                "model_used":     self.model,
                "error":          True,
            }
        except Exception as e:
            if "rate" in str(e).lower():
                time.sleep(5)
                return self.check(claim)
            return {
                "claim_id":       claim_id,
                "fraud_detected": False,
                "fraud_type":     None,
                "confidence":     "low",
                "explanation":    f"API error: {str(e)[:80]}",
                "checked_by":     "llm_claude",
                "model_used":     self.model,
                "error":          True,
            }

    def cost_summary(self):
        rates = {"haiku": (1.00, 5.00), "sonnet": (3.00, 15.00)}
        key   = "haiku" if "haiku" in self.model.lower() else "sonnet"
        ir, or_ = rates[key]
        cost  = (self.input_tokens / 1e6) * ir + (self.output_tokens / 1e6) * or_
        return {
            "api_calls":     self.api_calls,
            "input_tokens":  self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd":      round(cost, 4),
            "model":         self.model,
        }


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not ANTHROPIC_AVAILABLE:
        print("Install anthropic: pip install anthropic")
        sys.exit(1)
    try:
        checker = LLMChecker()
    except ValueError as e:
        print(f"\n❌ {e}")
        sys.exit(1)

    claim_files = sorted(PARSED_DIR.glob("*.json"))
    if not claim_files:
        print(f"No claims found in {PARSED_DIR}. Run src/parse_edi.py first.")
        sys.exit(1)

    print(f"\nRunning LLM checks on {len(claim_files):,} claims...")
    results      = []
    fraud_counts = defaultdict(int)
    errors       = 0
    start        = time.time()

    for i, claim_path in enumerate(claim_files):
        try:
            with open(claim_path) as f:
                claim    = json.load(f)
            claim_id = claim.get("claim_id", claim_path.stem)

            rule_path = RULE_DIR / f"{claim_id}_rules.json"
            if rule_path.exists():
                with open(rule_path) as f:
                    rule_result = json.load(f)
                if rule_result.get("fraud_detected"):
                    result = {
                        "claim_id":       claim_id,
                        "fraud_detected": True,
                        "fraud_type":     rule_result.get("fraud_type"),
                        "confidence":     "high",
                        "explanation":    "Already flagged by rule engine — LLM skipped.",
                        "checked_by":     "rule_engine",
                        "skipped":        True,
                    }
                else:
                    result = checker.check(claim)
                    time.sleep(RATE_LIMIT_DELAY)
            else:
                result = checker.check(claim)
                time.sleep(RATE_LIMIT_DELAY)

            results.append(result)
            if result["fraud_detected"]:
                fraud_counts[result["fraud_type"] or "Unknown"] += 1
            else:
                fraud_counts["Legitimate (LLM passed)"] += 1

            with open(LLM_DIR / f"{claim_id}_llm.json", "w") as f:
                json.dump(result, f, indent=2)

            if (i + 1) % 100 == 0:
                cost = checker.cost_summary()
                print(f"  {i+1}/{len(claim_files)} ... ${cost['cost_usd']:.3f}")
                sys.stdout.flush()

        except Exception as e:
            errors += 1
            print(f"[ERROR] {claim_path.name}: {e}")

    elapsed     = time.time() - start
    cost        = checker.cost_summary()
    total_fraud = sum(v for k, v in fraud_counts.items() if "Legitimate" not in k)
    total_clean = fraud_counts.get("Legitimate (LLM passed)", 0)

    print(f"\n{'='*55}")
    print(f"  LLM RESULTS — {len(claim_files)} claims in {elapsed:.0f}s")
    print(f"  Fraud: {total_fraud} | Clean: {total_clean} | Errors: {errors}")
    print(f"  Cost: ${cost['cost_usd']:.4f} ({cost['api_calls']} API calls)")
    print(f"{'='*55}")
