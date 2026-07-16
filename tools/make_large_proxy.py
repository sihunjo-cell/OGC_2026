"""Replicate a train instance's block list to emulate P6 scale (crash proxy).
Bays unchanged; blocks x mult with fresh ids. Release/due/proc/shape preserved."""
import copy
import json
import sys


def main(src, mult, out):
    prob = json.load(open(src, encoding="utf-8"))
    base = prob["blocks"]
    big = []
    for r in range(int(mult)):
        for b in base:
            nb = copy.deepcopy(b)
            nb["block_id"] = f"{b.get('block_id', 'b')}_{r}"
            big.append(nb)
    prob["blocks"] = big
    json.dump(prob, open(out, "w", encoding="utf-8"))
    print(f"wrote {out}: {len(big)} blocks ({len(base)}x{mult}), {len(prob['bays'])} bays")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
