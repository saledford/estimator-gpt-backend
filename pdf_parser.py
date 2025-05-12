import fitz  # PyMuPDF
import re

def extract_tables_from_pdf(file_path):
    doc = fitz.open(file_path)
    tables = []

    for page_num, page in enumerate(doc):
        text = page.get_text("text")
        lines = text.split('\n')
        for i, line in enumerate(lines):
            if re.search(r"(Door|Type|Qty|Size|Material)", line, re.IGNORECASE):
                table_rows = [line]
                for j in range(i + 1, len(lines)):
                    next_line = lines[j]
                    if next_line.strip() == "" or len(next_line.split()) < 2:
                        break
                    table_rows.append(next_line)

                tables.append({
                    "page": page_num + 1,
                    "header": line,
                    "rows": table_rows[1:]
                })
    return tables


def parse_door_schedule(table):
    door_count = 0
    sizes = []
    for row in table["rows"]:
        parts = re.split(r'\s{2,}|\t+', row.strip())
        for part in parts:
            if re.match(r"\d+'\d{1,2}\"x\d+'\d{1,2}\"", part) or re.match(r"\d[’']\d{1,2}[”\"]?[xX×]\d[’']\d{1,2}[”\"]?", part):
                sizes.append(part)
        for part in parts:
            if part.isdigit():
                door_count += int(part)
                break

    return {
        "scope": "Doors",
        "count": door_count,
        "sizes": sizes,
        "source_page": table["page"]
    }


def extract_structured_takeoff(file_path):
    raw_tables = extract_tables_from_pdf(file_path)
    structured_results = []
    for table in raw_tables:
        if "door" in table["header"].lower():
            parsed = parse_door_schedule(table)
            structured_results.append(parsed)

    return structured_results
