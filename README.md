# Record Matcher

A Python CLI tool for matching records across two collections of PDF documents. It extracts key fields from invoices, delivery notes, and other business documents, then finds matching pairs between collections using weighted scoring.

## Features

- **PDF Text Extraction**: Uses `pdfplumber` with `pdftotext` fallback
- **Supplier Detection**: Automatically identifies suppliers from document content
- **Field Extraction**: Extracts document numbers, customer references, dates, and amounts
- **Smart Matching**: Weighted scoring algorithm considering multiple signals
- **Confidence Levels**: HIGH, MEDIUM, and LOW confidence classifications
- **Extensible Patterns**: Support for custom supplier regex patterns via JSON config

## Supported Suppliers

Built-in patterns for: Applelec, Formed, James Latham, Aalco, Halifax Glass, Microkerf, Ottima, Woodworking Machinery, Lawcris, and Worldwide Express.

Unknown suppliers fall back to generic patterns.

## Installation

Requires Python 3.12+.

```bash
# Using uv
uv sync

# Or using pip
pip install pdfplumber
```

## Usage

```bash
python main.py --left /path/to/delivery_notes --right /path/to/invoices
```

### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `--left` | Yes | Directory containing the first collection of PDFs |
| `--right` | Yes | Directory containing the second collection of PDFs |
| `--config` | No | Path to custom supplier patterns JSON file |
| `--output` | No | Output file path (default: `match_results.json`) |

### Example

```bash
python main.py --left ./delivery_notes --right ./invoices --output results.json
```

## Matching Algorithm

Documents are scored based on weighted signals:

| Signal | Weight |
|--------|--------|
| Customer reference match | 35% |
| Cross-document reference | 20% |
| Same supplier | 15% |
| Document number in text | 10% |
| Date proximity (within 7 days) | 10% |
| Amount match | 10% |

### Confidence Levels

- **HIGH**: Score >= 0.50
- **MEDIUM**: Score >= 0.25
- **LOW**: Score > 0

## Output

Results are saved as JSON with:

- **summary**: Match statistics
- **matches**: Matched pairs with scores, signals, and warnings
- **unmatched**: Documents without matches

## Custom Patterns

Create a JSON file with supplier-specific regex patterns:

```json
{
  "My Supplier": {
    "name_match": "my\\s*supplier",
    "dn_number": "DN[\\s#]*(\\d+)",
    "invoice_number": "INV[\\s#]*(\\d+)",
    "customer_ref": "Ref:\\s*([\\w\\-]+)",
    "date": "Date:\\s*([\\d/]+)"
  }
}
```

Then run with:

```bash
python main.py --left ./left --right ./right --config patterns.json
```

## License

MIT
