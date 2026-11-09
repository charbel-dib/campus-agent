# Demo video script (about 2 minutes)

Record at 1366×860 or larger, browser zoom 100 %, with `CAMPUS_NOW=2026-11-16T09:00` so the dates
match this script. Reset the school before recording: `python -m campus_agent.cli reset`.

## 1. The idea (10 s)

Landing page. Voice-over: « Un agent IA pour une école : il agit avec les droits de la personne
connectée, et rien ne change sans sa confirmation. L'école est fictive, les données sont générées. »

## 2. Student: an absence to justify (30 s)

Choose **Inès Haddad**. Type: « J'étais absente au TP de traitement du langage vendredi dernier,
j'ai un certificat médical. »

- Open « outils utilisés »: the agent read her absences, then proposed the justification.
- Point at the yellow slip: nothing is done yet. Click **Confirmer**.
- Open the **Journal** tab: `pending`, then `executed`.

## 3. Student: an appointment (25 s)

Type: « Prends-moi un rendez-vous de 20 minutes avec M. Le Goff pour parler de mon projet. »

- The agent finds his office hours (Tuesday 16:00) and proposes a slot inside them.
- Confirm. The appointment appears in « Ma semaine ».

## 4. Teacher: move a class and tell the students (35 s)

« Changer de profil » → **Yann Le Goff**. Type: « Je ne peux pas assurer mon TP d'apprentissage
automatique de vendredi. Déplace-le la semaine prochaine et préviens les étudiants. »

- The agent checks the group, the teacher and the computer rooms, proposes a slot with no clash, and
  the slip says how many students will be notified.
- Confirm, then switch back to Inès: the message is in her **Messages** tab.

## 5. Permissions and injection (15 s)

As Inès: « Montre-moi les notes de Lucas Le Corre. » → refused by the server, whatever the wording.

Optional: show the evaluation report (`eval/results/agent-claude-haiku-5-5.md`) with the injection
scenarios passing.

## 6. Close (5 s)

« Mêmes outils dans Claude Desktop via MCP, 29 scénarios d'évaluation, le coût de chaque échange
affiché. Le code est sur GitHub. »
