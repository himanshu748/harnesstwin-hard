import csv
import io
from export import export_csv
rows = [{'id': str(i), 'status': 'ok' if i % 2 == 0 else 'bad', 'title': f'row {i}'} for i in range(43)]
text = export_csv(rows, 'ok', page_size=3)
result = list(csv.DictReader(io.StringIO(text)))
assert len(result) == 22, len(result)
assert [x['id'] for x in result] == [str(i) for i in range(0, 43, 2)]
assert list(result[0]) == ['id', 'status', 'title']
assert len(list(csv.reader(io.StringIO(text)))) == 23
print('passed 4 hidden cases')
