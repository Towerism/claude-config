"""Record which eval/criteria.json keys a runner reads, to check them against a suite's selectors.

A suite lists, as pointer selectors on the criteria file, the keys its runner reads (#92), so
an edit to another key leaves its record valid. These helpers let each runner's own fake-`claude`
test show that the runner reads nothing the selectors leave out.
"""

from eval import run_eval
from tests.checker_helpers import REPO_ROOT
from tools.cfg import eval_records as er


class _Recording(dict):
    """A criteria dict that notes the path of every value read out of it that isn't a group."""

    def __init__(self, data, path, reads):
        super().__init__(data)
        self._path = path
        self._reads = reads

    def __getitem__(self, key):
        value = super().__getitem__(key)
        path = f"{self._path}/{key}"
        if isinstance(value, dict):
            return _Recording(value, path, self._reads)
        self._reads.add(path)
        return value

    # Every other way to read a key goes through __getitem__ or is noted here, so a runner
    # that switches to .get(), `in` or a loop over .items() is still seen.
    def get(self, key, default=None):
        return self[key] if super().__contains__(key) else default

    def __contains__(self, key):
        self._reads.add(f"{self._path}/{key}")
        return super().__contains__(key)

    def items(self):
        return [(key, self[key]) for key in super().keys()]  # noqa: SIM118

    def values(self):
        return [self[key] for key in super().keys()]  # noqa: SIM118


def probe_reads(monkeypatch):
    """Patch the harness's criteria loader; the returned set fills with the keys read."""
    reads = set()
    real = run_eval.eval_criteria
    monkeypatch.setattr(run_eval, "eval_criteria", lambda ctx: _Recording(real(ctx), "", reads))
    return reads


def uncovered(pointers, reads):
    """The keys in `reads` that no pointer equals or contains, sorted."""
    return sorted(r for r in reads if not any(r == p or r.startswith(p + "/") for p in pointers))


def criteria_pointers(suite_name):
    """The pointers on eval/criteria.json that the repo's own gates give suite `suite_name`."""
    suites = er.parse_gates((REPO_ROOT / er.GATES_PATH).read_text())
    return [s["pointer"] for s in suites[suite_name].selectors if s["file"] == run_eval.CRITERIA]


def assert_reads_are_selected(suite_name, reads):
    assert reads, f"the {suite_name} run read no criteria key, so this check proved nothing"
    missing = uncovered(criteria_pointers(suite_name), reads)
    assert missing == [], f"{suite_name} reads {missing} but its selectors don't cover them"
