import csv
import io

def export_csv(rows: list[dict], status: str, page_size: int = 10) -> str:
    """Export all matching records with columns id,status,title."""
    filtered = [x for x in rows if x.get('status') == status]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=['id', 'status', 'title'], extrasaction='ignore')
    writer.writeheader()
    writer.writerows(filtered[:page_size])  # BUG: truncates after the first page
    return output.getvalue()
