# Data Folder

Put lead files here.

Supported formats:

- CSV: recommended
- XLSX: supported through `openpyxl`

Required logical columns:

- business name
- email
- phone or whatsapp

The column names do not need to match exactly. The app uses the aliases in
`config.COLUMN_MAP`.

Generated files:

- `sent_log.csv`: delivery history
- `previews/`: rendered email previews
