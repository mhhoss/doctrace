"""Generates the four corpus files this eval uses, from the text defined here.

Run once (`uv run python eval/extraction_corpus/generate_corpus.py`); its output is
committed alongside this script so the corpus is reproducible and its provenance is
explicit — see this directory's `manifest.json` for what each file actually is
(synthetic vs. real DOCX) and `ground_truth.json`'s `"authored_by"` note.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tests.docx_fixtures import build_docx
from tests.pdf_fixtures import build_pdf

OUT_DIR = Path(__file__).resolve().parent

# --- doc-en-pdf-service-agreement: English, PDF, 3 pages ---

EN_PDF_PAGE_1 = """Section 1. Scope of Services
The Contractor shall provide software maintenance services as described in Exhibit A.

Section 2. Deliverables
The Contractor must deliver a monthly status report no later than the fifth business day of each month.
The Contractor shall complete the initial system audit within 30 days of the Effective Date.

Section 3. Payment
The Client shall pay all undisputed invoices within 15 days of receipt.
Late payments shall accrue interest at a rate of 1.5% per month."""

EN_PDF_PAGE_2 = """Section 4. Confidentiality
Each party shall keep the other party's Confidential Information strictly confidential.
Neither party shall disclose Confidential Information to any third party without prior written consent.

Section 5. Termination
Either party may terminate this Agreement with 60 days' written notice.
The Client may terminate immediately upon a material breach that remains uncured for 10 days after notice.

Section 6. Liability
Neither party shall be liable for indirect or consequential damages.
The Contractor's total liability shall not exceed the fees paid in the preceding six months."""

EN_PDF_PAGE_3 = """Section 7. Warranties
The Contractor warrants that all services will be performed in a professional and workmanlike manner.
"Confidential Information" means any non-public information disclosed by either party in connection with this Agreement.

Section 8. Governing Law
This Agreement shall be governed by the laws of the jurisdiction in which the Client is incorporated.
The Contractor shall not assign this Agreement without the Client's prior written consent."""

# --- doc-fa-pdf-tender: Persian, PDF, 3 pages, clause-numbered (ماده) ---

FA_PDF_PAGE_1 = """ماده ۱: پیمانکار موظف است کار را ظرف نود روز از تاریخ ابلاغ قرارداد تکمیل نماید.

ماده ۲: پیمانکار باید ضمانت‌نامه انجام تعهدات به میزان ده درصد مبلغ قرارداد ارائه دهد.

ماده ۳: کارفرما موظف است پیش‌پرداخت را ظرف پانزده روز از امضای قرارداد واریز نماید."""

FA_PDF_PAGE_2 = """ماده ۴: پیمانکار حق واگذاری قرارداد به شخص ثالث را بدون موافقت کتبی کارفرما ندارد.

ماده ۵: در صورت تاخیر در تحویل، جریمه‌ای معادل نیم درصد مبلغ قرارداد به ازای هر روز تاخیر اعمال می‌شود.

ماده ۶: پیمانکار موظف است گزارش پیشرفت کار را هر ماه به کارفرما ارائه دهد."""

FA_PDF_PAGE_3 = """ماده ۷: «مدت قرارداد» به معنای بازه زمانی از تاریخ ابلاغ تا تحویل قطعی است.

ماده ۸: پیمانکار باید کلیه پرسنل خود را بیمه نماید.

ماده ۹: کارفرما می‌تواند در صورت تخلف پیمانکار، قرارداد را فسخ نماید."""

# --- doc-mixed-docx-it-project: Persian-dominant, DOCX, inline English terms ---

MIXED_DOCX_PARAGRAPHS = [
    "این قرارداد میان کارفرما و پیمانکار برای پیاده‌سازی سیستم Kubernetes منعقد می‌شود.",
    "پیمانکار موظف است محیط Staging را ظرف دو هفته از شروع پروژه راه‌اندازی کند.",
    "پیمانکار باید مستندات Deployment را به زبان فارسی و انگلیسی تحویل دهد.",
    "کارفرما موظف است دسترسی به سرورهای Production را ظرف پنج روز کاری فراهم کند.",
    "پیمانکار حق ندارد بدون اجازه کارفرما اطلاعات پیکربندی را با اشخاص ثالث به اشتراک بگذارد.",
    "در صورت بروز Downtime بیش از یک ساعت، پیمانکار موظف به ارائه گزارش علت و راه‌حل ظرف ۲۴ ساعت است.",
]

# --- doc-en-docx-data-policy: English, DOCX ---

EN_DOCX_PARAGRAPHS = [
    "Employees must complete data protection training within 30 days of joining.",
    "Personal data shall not be stored on unencrypted portable devices.",
    "\"Personal Data\" means any information relating to an identified or identifiable individual.",
    "Any data breach must be reported to the Data Protection Officer within 24 hours of discovery.",
    "Employees are prohibited from sharing login credentials with any other person.",
    "Access to customer records shall be limited to employees with a documented business need.",
    "Backup copies of production data must be retained for a minimum of 90 days.",
]


def main() -> None:
    # Generous width: these lines are full sentences, longer than pdf_fixtures.py's
    # own short-sentence tests were tuned for — pdftotext -layout clips a line at the
    # page edge rather than wrapping it, so an under-wide page silently truncates text.
    wide = 20000
    (OUT_DIR / "doc-en-pdf-service-agreement.pdf").write_bytes(
        build_pdf(
            [EN_PDF_PAGE_1, EN_PDF_PAGE_2, EN_PDF_PAGE_3],
            visual_order=False,
            page_width=wide,
        )
    )
    (OUT_DIR / "doc-fa-pdf-tender.pdf").write_bytes(
        build_pdf([FA_PDF_PAGE_1, FA_PDF_PAGE_2, FA_PDF_PAGE_3], page_width=wide)
    )
    (OUT_DIR / "doc-mixed-docx-it-project.docx").write_bytes(
        build_docx(MIXED_DOCX_PARAGRAPHS)
    )
    (OUT_DIR / "doc-en-docx-data-policy.docx").write_bytes(
        build_docx(EN_DOCX_PARAGRAPHS)
    )
    print(f"Wrote 4 corpus files to {OUT_DIR}")


if __name__ == "__main__":
    main()
