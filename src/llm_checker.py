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
    if any(icd in icd10_codes for icd in RESPIRATORY_INFECTIONS) and \
       any(c in cpt_codes for c in ["93458","93454","0026U","0048U","0006M","0007M","0019M"]):
        critical.append(
            "CRITICAL: J06.9/J00/J20.9 are respiratory infections (cold, bronchitis). "
            "Invasive cardiac or high-cost genomic procedures billed with these diagnoses "
            "are ALWAYS phantom billing or diagnosis mismatch — flag with HIGH confidence."
        )
    if any(icd in icd10_codes for icd in WELLNESS_CODES) and total_charge > 800 and \
       not any(c in cpt_codes for c in ["99205","99204","99203","G0438","G0439"]):
        critical.append(
            "CRITICAL: Z00.00 is routine wellness exam. Specialty procedures over $800 billed "
            "with ONLY Z00.00 and no legitimate specialty indication are phantom billing."
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

STEP 3 — UPCODING CHECK (E&M CODES ONLY)
Is the PRIMARY code an E&M visit code (99202–99215)?
→ If NO: skip to Step 4. Upcoding NEVER applies to imaging, cardiac, lab, surgical.
→ If YES: Does the diagnosis justify the SPECIFIC complexity level billed?
  (99215 = high complexity, 99213 = moderate, 99212 = low)
  AND does billed price exceed 400% of Medicare rate?
  → If both true: Upcoding, MEDIUM confidence.

STEP 4 — DIAGNOSIS MISMATCH CHECK
Ask: "Is there ANY valid clinical pathway from this diagnosis to this procedure?"
Consider: referral chains, comorbidities, screening for at-risk patients.
Examples of LEGITIMATE unusual combos:
  - 71046 (chest X-ray) + J06.9 (URI) → LEGITIMATE (rule out pneumonia)
  - 93000 (ECG) + I10 (hypertension) → LEGITIMATE (cardiac monitoring)
  - 29881 (knee arthroscopy) + M25.511 (shoulder pain) → MISMATCH (wrong joint)
  - 70553 (brain MRI) + N39.0 (UTI) → MISMATCH (no neurological indication)
Only flag if you are CERTAIN there is NO valid clinical relationship.
→ If certain mismatch: Diagnosis Mismatch, HIGH confidence.
→ If uncertain: fraud_detected=false.

STEP 5 — CODE PADDING CHECK
Is there a legitimate primary procedure PLUS additional codes that are
clearly unrelated to the diagnosis and serve only to inflate the total?
Unrelated = completely different organ system or clinical context.
→ If yes: Code Padding, HIGH confidence.

STEP 6 — LEGITIMATE
If none of the above apply → fraud_detected=false.
High-price specialty procedures with matching diagnoses are NEVER fraud.

FEW-SHOT EXAMPLES:

Example A — Diagnosis Mismatch (NOT Upcoding):
  CPT: 93458 (cardiac cath), ICD: N39.0 (UTI), Billed: $3,400
  → Cardiac catheterization requires a cardiac indication. UTI has no cardiac
    relationship. fraud_type = "Diagnosis Mismatch", confidence = "high"

Example B — Legitimate (not upcoding despite high price):
  CPT: 93306 (complete echo), ICD: I25.10 (CAD), Billed: $1,100
  → Echo is single-level specialty, CAD is a perfect cardiac indication.
    fraud_detected = false

Example C — Upcoding (E&M only):
  CPT: 99215 (high complexity office visit), ICD: J06.9 (cold), Billed: $280
  → 99215 requires high medical decision-making; a cold warrants 99213.
    Billed at 320% Medicare rate. fraud_type = "Upcoding", confidence = "medium"

Example D — Code Padding:
  CPT: 99202 + 0026U + 0063M, ICD: M25.511 (shoulder pain), Billed: $8,400
  → 0026U/0063M are oncology genomic assays. Shoulder pain has no oncology
    relationship. fraud_type = "Code Padding", confidence = "high"

Example E — Phantom Billing:
  CPT: 93458 (cardiac cath), ICD: Z00.00 (routine wellness), Billed: $3,200
  → Invasive cardiac procedure during wellness visit — clinically impossible.
    fraud_type = "Phantom Billing", confidence = "high"

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

            rule_path = RULE_DIR / f"{claim_id}_rule.json"
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
