import json
import pytest
from click.testing import CliRunner


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


@pytest.fixture
def make_llm_response():
    """Factory fixture: returns a mock ollama.chat response dict with given values."""
    def _make(action="BUY", confidence=0.8, qty=10,
               reason="Strong fundamentals", stop_pct=0.07):
        return {
            'message': {
                'content': json.dumps({
                    'action': action,
                    'confidence': confidence,
                    'qty': qty,
                    'reason': reason,
                    'stop_pct': stop_pct,
                })
            }
        }
    return _make


@pytest.fixture
def mock_buffett_strong():
    """High-score Buffett metrics dict (score=80, all criteria met)."""
    return {
        'score': 80,
        'roe': 0.22,
        'roic': 0.18,
        'debt_equity': 0.4,
        'op_margin': 0.25,
        'fcf_yield': 0.04,
        'pe': 22.0,
        'pb': 3.5,
        'div_yield': 0.015,
    }


@pytest.fixture
def in_memory_db(monkeypatch, tmp_path):
    """Patch DB_PATH to a temp file, initialize the schema, yield the path."""
    db_path = str(tmp_path / 'test.db')
    monkeypatch.setattr('buffet_bot.db.DB_PATH', db_path)
    from buffet_bot.db import init_db
    init_db()
    yield db_path
