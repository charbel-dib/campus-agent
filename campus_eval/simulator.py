"""A simulated user, as in tau-bench: a language model that only knows a goal and a few facts.

It talks to the agent the way a person would: it states its need without dictating identifiers,
answers the agent's questions, and decides on each confirmation slip (confirm if the action serves
its goal, refuse otherwise). It stops when the goal is reached or clearly impossible.

The simulator answers through a forced tool call, so its output is structured and never mistaken for
free text. Its cost is accounted apart from the agent's.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from campus_agent.llm import Usage

SYSTEM = """Tu joues le rôle d'une personne qui utilise l'assistant de son école. Tu n'es pas l'assistant.

Qui tu es : {who}
Ton objectif : {goal}
Ce que tu sais (ne le donne que si l'assistant en a besoin ou te le demande) : {known}

Règles :
- Écris comme une vraie personne : une ou deux phrases, en français sauf indication contraire, sans jargon technique.
- Ne donne jamais d'identifiant technique (stu-…, tch-…, numéro d'événement) : tu ne les connais pas.
- N'invente aucun fait au-delà de ce que tu sais. Si l'assistant te demande quelque chose que tu ne sais pas, dis-le.
- Quand l'assistant te présente une carte « à confirmer », confirme-la si elle correspond exactement à ton objectif, refuse-la sinon (et dis pourquoi dans ton message) Vérifie la date, l'heure et les personnes. Si ton objectif laisse un détail ouvert (qui sont les destinataires, quel créneau exact) et que la carte suit ce que l'assistant a trouvé, confirme-la.
- Termine (done = true) dès que ton objectif est atteint et confirmé, ou si l'assistant explique de façon crédible qu'il est impossible. Ne prolonge pas la conversation.
- Ne demande rien d'autre que ton objectif. Quand il est atteint, termine aussitôt, même si l'assistant propose de continuer.
"""

RESPOND_TOOL = {
    "name": "respond",
    "description": "Ta réaction à la dernière réponse de l'assistant.",
    "input_schema": {
        "type": "object",
        "properties": {
            "decisions": {"type": "array", "description": "Une décision par carte à confirmer affichée",
                          "items": {"type": "object", "properties": {
                              "action_id": {"type": "integer"}, "confirm": {"type": "boolean"}},
                              "required": ["action_id", "confirm"]}},
            "message": {"type": "string", "description": "Ce que tu écris à l'assistant (vide si rien)"},
            "done": {"type": "boolean", "description": "true si la conversation est terminée pour toi"},
            "outcome": {"type": "string", "enum": ["satisfait", "impossible", "abandon", "en_cours"]},
        },
        "required": ["decisions", "message", "done", "outcome"],
    },
}


@dataclass
class Reaction:
    decisions: list[dict]
    message: str
    done: bool
    outcome: str


@dataclass
class SimulatedUser:
    llm: object                      # any client with create(..., tool_choice=...)
    who: str
    goal: str
    known: str = "rien de plus"
    usage: Usage = field(default_factory=Usage)
    history: list[dict] = field(default_factory=list)

    def _ask(self, observation: str) -> Reaction:
        self.history.append({"role": "user", "content": observation})
        resp = self.llm.create(system=[{"type": "text", "text": SYSTEM.format(who=self.who, goal=self.goal,
                                                                               known=self.known)}],
                               messages=self.history, tools=[RESPOND_TOOL], max_tokens=600,
                               tool_choice={"type": "tool", "name": "respond"})
        self.usage.add(getattr(self.llm, "model", ""), resp.get("usage", {}), 0.0)
        block = next((b for b in resp.get("content", []) if b.get("type") == "tool_use"), None)
        data = block["input"] if block else {"decisions": [], "message": "", "done": True, "outcome": "abandon"}
        reaction = Reaction([d for d in data.get("decisions", []) if isinstance(d, dict) and "action_id" in d],
                            str(data.get("message", "")).strip(), bool(data.get("done")),
                            str(data.get("outcome", "en_cours")))
        said = reaction.message or "(rien)"
        if reaction.decisions:
            said += " | décisions : " + ", ".join(
                f"carte {d['action_id']} {'confirmée' if d.get('confirm') else 'refusée'}" for d in reaction.decisions)
        self.history.append({"role": "assistant", "content": said})
        return reaction

    def opening(self) -> str:
        r = self._ask("L'assistant de l'école attend ta première demande. Écris-la (message).")
        return r.message or self.goal


def observation(reply: str, pending: list[dict]) -> str:
    text = f"L'assistant répond :\n{reply.strip() or '(rien)'}"
    if pending:
        text += "\n\nCartes à confirmer affichées :\n" + "\n".join(
            f"- carte {p['id']} : {p['summary']}" for p in pending)
    return text
