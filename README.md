# MM Click Analyzer

Streamlit app for analyzing click + stats data, bucketing performance, and recommending bid adjustments.

## Running locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

For development (tests, linting):

```bash
pip install -r requirements-dev.txt
pytest
```

## Project layout

```
├── app.py              # Streamlit entry point (formerly mm-click-analyzer-demo.py)
├── config.py           # Constants, defaults, brand colors, session-state keys
├── data_utils.py       # Column matching, numeric coercion, device/ID normalization
├── business_logic.py   # BucketingPolicy, efficiency metrics, bid transforms
├── file_operations.py  # CSV/Excel reads (cached), preset save/load, modifier export
├── ui_components.py    # CSS, tooltip badges, session helpers
└── tests/              # pytest suite
```

`app.py` is the only Streamlit-driven module; everything else is pure Python and importable without a Streamlit runtime.

## Presets

The sidebar lets you save the current sliders/thresholds to a JSON file and load them back later. Presets are versioned (`_preset_version: 1`) and size-capped; unknown keys are ignored with a warning rather than silently overwriting state. Flat-dict presets from earlier builds still load.

## Bucketing

`BucketingPolicy` (in `business_logic.py`) decides each source's `Perf Bucket` ∈ {Top, Average, Weak, No Quote Starts} based on CPQS / Cost-per-Lead / Worst-of-all, then maps that bucket to a `Recommended Bid %`. The vectorized `apply_policy_to_dataframe` produces the same result as the per-row path but replaces `DataFrame.apply(axis=1)` with a handful of boolean masks — much faster on large frames.

Volume gating (optional): rows with fewer clicks than the configured threshold are forced into `Average` regardless of their efficiency score.

## Testing

```bash
pytest                    # full suite
pytest tests/test_business_logic.py -v
```

The vectorized bucketing path is explicitly compared against the scalar path in `TestApplyPolicyVectorized` to guard against regressions.
