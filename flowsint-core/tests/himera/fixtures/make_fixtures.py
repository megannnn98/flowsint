"""
Generate synthetic Himera-like PDFs for tests.

All people, numbers and addresses here are fictional. Run once and commit the
output; tests only read the committed PDFs:

    uv run python flowsint-core/tests/himera/fixtures/make_fixtures.py
"""

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

HERE = Path(__file__).parent
FONT = "/usr/share/fonts/TTF/DejaVuSerifCondensed.ttf"
FONT_BOLD = "/usr/share/fonts/TTF/DejaVuSerifCondensed-Bold.ttf"

# Each page: list of lines. A line starting with "#" is a bold heading.
PAGES = [
    [
        "Отчет № 00000000-0000-4000-8000-000000000001",
        "#УСТАНОВОЧНЫЕ ДАННЫЕ",
        "Лица                  ПЕТРОВ ПЁТР СЕРГЕЕВИЧ 3.4.1985,",
        "Паспорта              0000123456",
        "Телефоны              79990001122, 74950001122",
        "СНИЛС                 11223344595",
        "ИНН                   770123456703",
        "Возможные фамилии     ПЕТРОВ, ПЕТРОВЪ",
        "#Результаты скоринга",
        "Общий показатель                      0,8 - есть негатив",
        "Прочее                                0,8",
        "#Метки",
        "Имя              ПЕТРОВ ПЁТР СЕРГЕЕВИЧ",
        "Дата рождения    3.4.1985",
        "Регион           ТВЕРСКАЯ ОБЛАСТЬ",
        "Метки            ДОЛЖНИК",
        "#Доходы",
        "Место работы                     Сумма       Дата актуальности",
        "ООО ВЕКТОР                       120000 ₽    2021",
        "#Адреса",
        "Г ТВЕРЬ, УЛ ЛЕСНАЯ, Д 5, КВ 12",
        "ТВЕРЬ, ЛЕСНАЯ УЛ., Д.5, КВ.12",
    ],
    [
        "#КЛИЕНТЫ БАНКА 2023 QV",
        "Имя                   ПЕТРОВ ПЁТР СЕРГЕЕВИЧ",
        "Дата рождения         3.4.1985",
        "Телефон               79990001122",
        "Емаil                 PETROV85@МАIL.RU",
        "#КЛИЕНТЫ БАНКА 2023 QV",
        "Имя                   ПЕТРОВ ПЁТР СЕРГЕЕВИЧ",
        "Дата рождения         3.4.1985",
        "Телефон               79990001122",
        "Емаil                 PETROV85@МАIL.RU",
        "#КАДРЫ ФЛ 2021 10 LU",
        "Имя                   ПЕТРОВ ПЁТР СЕРГЕЕВИЧ",
        "Дата рождения         3.4.1985",
        "ИНН                   770123456703",
        "СНИЛС                 11223344595",
    ],
    [
        "Организация           ООО ВЕКТОР",
        "ИНН организации       7709876545",
        "ОГРН                  1027700123450",
        "Должность             ИНЖЕНЕР",
        "#ПОЛЬЗОВАТЕЛИ СОЦСЕТИ 2020 VK",
        "Имя                   Петров Пётр",
        "Дата рождения         03.04.1985",
        "Профиль               https://vk.com/id123456789",
        "Телеграм              @petrov_ps",
        "E-mail                petrov.ps@example.com",
        "Телефон               +7 (916) 555-12-34",
        "Логин                 petrov_ps",
    ],
    [
        "#Жители Москвы 2021 (совпадения по фио)",
        "Имя                   Петров Пётр Сергеевич",
        "Номер документа       4500999888",
        "Телефон               79160000000",
        "Email                 p.petrov@example.org",
        "Адрес                 Москва, ул. Садовая, д.1, кв.2",
        "",
        "Отчёт может быть урезан из за его размера, для полноты отчёта скачайте txt.",
        "Отчёт был сформирован в сервисе Himera Search (synthetic fixture).",
    ],
]


def make_text_pdf(path: Path) -> None:
    pdfmetrics.registerFont(TTFont("DV", FONT))
    pdfmetrics.registerFont(TTFont("DVB", FONT_BOLD))
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle("synthetic himera report")
    width, height = A4
    for lines in PAGES:
        y = height - 60
        for line in lines:
            if line.startswith("#"):
                c.setFont("DVB", 12)
                c.drawString(50, y, line[1:])
                y -= 22
            else:
                c.setFont("DV", 10)
                c.drawString(50, y, line)
                y -= 16
        c.showPage()
    c.save()


def make_scanned_pdf(path: Path) -> None:
    """Pages with only vector shapes and no text layer (stands in for a scan)."""
    c = canvas.Canvas(str(path), pagesize=A4)
    for _ in range(2):
        for i in range(20):
            c.rect(50, 780 - i * 30, 400 - i * 7, 10, fill=1)
        c.showPage()
    c.save()


if __name__ == "__main__":
    make_text_pdf(HERE / "synthetic_report.pdf")
    make_scanned_pdf(HERE / "scanned_report.pdf")
    print("written", sorted(p.name for p in HERE.glob("*.pdf")))
