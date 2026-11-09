"""Talk to the agent from a terminal.

    python -m campus_agent.cli personas
    python -m campus_agent.cli chat --as stu-0001
    python -m campus_agent.cli ask --as tch-001 "Déplace mon TP de vendredi à la semaine prochaine" --yes
    python -m campus_agent.cli reset            # regenerate the school
"""

from __future__ import annotations

import argparse
import json
import sys

from .llm import LLMError
from .runtime import boot

PERSONAS = [
    ("stu-0001", "Inès Haddad, étudiante en 3e année, parcours IA"),
    ("stu-0003", "Maël Guivarc'h, étudiant en 1re année"),
    ("tch-001", "Yann Le Goff, enseignant (apprentissage automatique, projet IA)"),
    ("tch-008", "Hugo Lefèvre, enseignant (analyse, 1re année)"),
    ("stf-01", "Claire Morvan, responsable de la scolarité"),
]


def _print_turn(turn, verbose: bool) -> None:
    if verbose:
        for step in turn.trace:
            if step["type"] == "tool":
                mark = "ok " if step["ok"] else "ERR"
                print(f"  [{mark}] {step['name']}({json.dumps(step['input'], ensure_ascii=False)}) → {step['preview']}")
    print(f"\n{turn.reply}\n")
    u = turn.usage
    print(f"  ({u.calls} appel(s), {u.input_tokens} jetons en entrée, {u.cache_read_tokens} lus en cache, "
          f"{u.output_tokens} en sortie, {u.cost_usd * 100:.3f} centimes $)")


def _decide_all(agent, sid, actor, turn, auto: bool | None, verbose: bool) -> None:
    while turn.pending_actions:
        action = turn.pending_actions[0]
        print(f"\n  ⏸  Action à confirmer : {action['summary']}")
        if auto is None:
            answer = input("  Confirmer ? [o/N] ").strip().lower()
            ok = answer in ("o", "oui", "y", "yes")
        else:
            ok = auto
            print("  → " + ("confirmée" if ok else "refusée") + " automatiquement")
        turn = agent.decide(sid, actor, action["id"], ok)
        _print_turn(turn, verbose)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="campus_agent.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("personas")
    sub.add_parser("reset")
    chat = sub.add_parser("chat")
    chat.add_argument("--as", dest="actor", default="stu-0001")
    chat.add_argument("-v", "--verbose", action="store_true")
    ask = sub.add_parser("ask")
    ask.add_argument("message")
    ask.add_argument("--as", dest="actor", default="stu-0001")
    ask.add_argument("--yes", action="store_true", help="confirmer automatiquement les actions proposées")
    ask.add_argument("--no", action="store_true", help="refuser automatiquement les actions proposées")
    ask.add_argument("--model")
    ask.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    if args.cmd == "personas":
        for pid, label in PERSONAS:
            print(f"{pid:9} {label}")
        return
    rt = boot(reset=args.cmd == "reset")
    if args.cmd == "reset":
        print(f"École régénérée : {rt.db_path}")
        return
    conn = rt.connect()
    try:
        llm = rt.llm(getattr(args, "model", None))
    except LLMError as exc:
        sys.exit(str(exc))
    agent = rt.agent(conn, llm)
    sid = agent.new_session(args.actor)
    if args.cmd == "ask":
        turn = agent.run(sid, args.actor, args.message)
        _print_turn(turn, args.verbose)
        auto = True if args.yes else (False if args.no else None)
        _decide_all(agent, sid, args.actor, turn, auto, args.verbose)
        return
    print("Tape ta demande (Ctrl+D pour quitter).")
    for line in sys.stdin:
        if not line.strip():
            continue
        turn = agent.run(sid, args.actor, line)
        _print_turn(turn, args.verbose)
        _decide_all(agent, sid, args.actor, turn, None, args.verbose)


if __name__ == "__main__":
    main()
