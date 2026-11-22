from pathlib import Path
import sys
root = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(root/'R1/shared/policy_mapper'), str(root/'R2/policy')]
