"""
Profile validation of every sample Bundle with HAPI's validator and the ABDM FHIR IG (the real conformance check).

Slow (the validator loads the IG and the R4 core packages: minutes the first time) and needs Java 17 plus two
downloads, so it only runs when they are configured:

    HAPI_VALIDATOR_JAR=/path/validator_cli.jar         https://github.com/hapifhir/org.hl7.fhir.core/releases
    NRCES_IG_PACKAGE=/path/package.tgz                 https://nrces.in/ndhm/fhir/r4/package.tgz  (IG 6.5.0)
    HAPI_TX=n/a                                        optional: skip terminology (no network); default tx.fhir.org
    JAVA=/path/java                                    optional, default `java`

    pytest tests/fhir/test_hapi_validation.py
"""
import json
import os
import shutil
import subprocess

import pytest

from src.app import fhir
from tests.fhir.samples import HIP_ID, sample

JAR = os.environ.get("HAPI_VALIDATOR_JAR")
IG = os.environ.get("NRCES_IG_PACKAGE")
JAVA = os.environ.get("JAVA", "java")
TX = os.environ.get("HAPI_TX", "https://tx.fhir.org")

pytestmark = pytest.mark.skipif(
    not (JAR and IG and os.path.exists(JAR or "") and os.path.exists(IG or "") and shutil.which(JAVA)),
    reason="set HAPI_VALIDATOR_JAR and NRCES_IG_PACKAGE (and have Java) to run the HAPI profile validation")

# with `-tx n/a` the validator cannot expand value sets (mime types, currencies), and a resource that fails one of them
# is then also "not found" by the profile slice that points at it; those are the only messages ignored
_OFFLINE_NOISE = ("could not be determined for the code", "was not found in the value set", "Unable to validate code",
                  "A definition for CodeSystem", "Unable to find a profile match",
                  "Error from http")  # a terminology server the validator has cached an answer from


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory):
    folder = tmp_path_factory.mktemp("bundles")
    for hi_type in fhir.RECORD_TYPES:
        (folder / f"{hi_type}.json").write_text(json.dumps(fhir.build_record(hi_type, sample(hi_type), HIP_ID)),
                                                encoding="utf-8")
    report = folder / "report.json"
    command = [JAVA, "-Dfile.encoding=UTF-8", "-jar", JAR, *sorted(str(p) for p in folder.glob("*.json")
                                                                    if p.name != "report.json"),
               "-version", "4.0.1", "-ig", IG, "-tx", TX, "-output", str(report)]
    run = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    assert report.exists(), run.stdout[-2000:] + run.stderr[-2000:]
    result = {}
    for entry in json.loads(report.read_text(encoding="utf-8"))["entry"]:
        outcome = entry["resource"]
        name = next(e["valueString"] for e in outcome["extension"] if "validation" in e["url"] or "valueString" in e)
        errors = [i for i in outcome["issue"] if i["severity"] in ("error", "fatal")]
        if TX == "n/a":
            errors = [i for i in errors if not any(n in i["details"]["text"] for n in _OFFLINE_NOISE)]
        result[os.path.splitext(os.path.basename(name))[0]] = [
            f"{(i.get('expression') or ['?'])[0]}: {i['details']['text']}" for i in errors]
    return result


@pytest.mark.parametrize("hi_type", fhir.RECORD_TYPES)
def test_bundle_has_no_profile_errors(outcomes, hi_type):
    assert outcomes[hi_type] == []
