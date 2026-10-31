#!/usr/bin/env python3
"""Pre-submit gate over an aggregate (aggregate.py output). Exit 1 if any blocking kind fired.

  python3 gate.py agg.json [--allow kind,kind] [--quiet]
  --quiet prints counts only (CI logs on a public repo); the full list is in the sealed bundle.

BLOCK: anything App Review can hit by tapping around: leaving the app without asking, a hidden
code answering, a crash, a dead end, a stuck screen, a sheet you cannot close, paid features
without a purchase, developer text, a tap that does nothing.
WARN: printed, not failing (design calls the owners make).
"""
import json, sys

BLOCK = {"escape_unintended", "hidden_code_path", "js_uncaught", "js_error", "unhandled_rejection", "console_error",
         "dead_end_non_app_page", "dead_end_no_controls", "dead_end_legal_page", "orphan_sheet", "stacked_sheets",
         "stuck_loading", "long_scan", "counter_past_free_limit", "pro_without_purchase",
         "developer_mode_on", "developer_text_on_screen", "raw_value_on_screen", "download_in_webview", "external_request",
         "control_does_nothing", "sheet_clipped", "overflow_x", "paywall_no_plans", "page_load_error", "toast_covers_header"}
WARN = {"escape_unlabeled", "paywall_for_subscriber", "free_scan_not_counted", "pill_mismatch", "filter_always_empty", "legal_page_back_column", "free_scans_reset"}


def main():
    agg = json.loads(open(sys.argv[1]).read())
    allow = set()
    if "--allow" in sys.argv:
        allow = set(sys.argv[sys.argv.index("--allow") + 1].split(","))
    t = agg["totals"]
    print("walks %d, UI actions %d, sweep taps %d, unique failures %d" % (t.get("walks", 0), t.get("actions", 0),
                                                                        agg.get("sweep", {}).get("taps", 0), t.get("unique_failures", 0)))
    bad = 0
    quiet = "--quiet" in sys.argv
    for s in agg["signatures"]:
        k = s["kind"]
        tag = "BLOCK" if k in BLOCK and k not in allow else "WARN" if k in WARN or k in allow else "INFO"
        if tag == "BLOCK":
            bad += 1
        if not quiet:
            print("%-5s %4d walks %3d sweep  %s" % (tag, s["walks"], s.get("sweep_hits", 0), s["sig"][:150]))
    if t.get("harness_errors", 0) > max(5, t.get("walks", 0) // 100):
        print("FAIL: too many harness errors (%d): the run itself is not trustworthy" % t["harness_errors"])
        bad += 1
    print("GATE %s: %d blocking signature(s)" % ("FAIL" if bad else "PASS", bad))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
