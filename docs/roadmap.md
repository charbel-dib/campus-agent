# Campus Agent — feuille de route

Document de travail, rédigé le 9 novembre 2026 et maintenu à chaque bloc livré. Il décrit tout ce qui reste à faire, dans l'ordre, pour transformer la démo actuelle en une preuve défendable : « je sais construire, évaluer, exploiter et maintenir un agent en production, avec ses aspects métier ». Rien ici n'est encore implémenté, sauf ce qui est marqué **[fait]**.

Ce document sert à trois choses : piloter le travail bloc par bloc, alimenter la documentation du dépôt au fur et à mesure, et fournir les arguments des candidatures (CV, page de démo, entretiens).

---

## État au 10 novembre 2026 (version 2.0.0)

Ce qui suit dans ce document est le plan d'origine. Voici où il en est. « Fait » veut dire : codé, testé,
documenté. Ce qui n'est pas fait n'est revendiqué nulle part (README, page de démo, CV).

| Bloc | Fait | Non fait, ou fait autrement |
|---|---|---|
| 0. Deux dépôts | Dépôt RAG séparé, `pyproject.toml`, ruff, README anglais | — |
| A. Évaluation | 170 tâches, 9 suites, utilisateur simulé, pass^k, canaris, injection, ancrage, intervalles, verrou CI, rapport, comparaison Sonnet | Juge LLM calibré ; API Batches ; jeu adverse écrit par un tiers ; commentaire automatique sur la PR |
| B. Exploitation | Traces GenAI (stockées, export OTLP), onglet Pilotage, retours, relais humain (bouton), consignes versionnées, drapeaux, SLO, runbook, export / effacement / purge | PostgreSQL ; alertes automatiques ; journaux JSON corrélés ; drapeaux par rôle |
| C. Sécurité | Modèle de menaces, tableau OWASP, un test nommé par contrôle, MCP distant avec jetons liés à la ressource, `security.yml`, Dependabot | Enregistrement des clients (côté fournisseur d'identité) ; SBOM attaché à une release (il est produit en CI) |
| D. Intégrations | iCalendar (flux et import), boîte d'envoi vers SMTP / CalDAV / GLPI, Moodle, OIDC, fichier compose | GLPI et Moodle vérifiés sur la forme documentée de leurs API, pas sur un serveur ; compose et Keycloak non exercés en CI ; Microsoft Graph décrit, non codé |
| E. Métier | Conventions de stage, saisie et publication de notes, inscription au rattrapage, guides par profil | PDF de la convention, relevé de notes, convocations ; règlement et interface en anglais (l'agent répond en anglais) |
| F. Avancé | Étapes d'outils en direct (SSE) ; escalade de modèle, questions à choix, procédures à la demande, cache d'une heure (désactivés par défaut) | Texte de la réponse en flux continu ; mémoire utilisateur ; mesure comparative des options ; utilisateur simulé local |
| Démo | Page mise à jour, onglets Coulisses et Pilotage, `/api/status` | Vidéo ; parcours guidé |
| Documentation | Tout le tableau du §10 | — |

---

## 0. Point de départ et cible

### Ce qui existe aujourd'hui [fait]

- Dépôt `charbel-dib/ensta_chatbot`, branche `main`, 68 commits, 19 Mo dont 17 Mo de données du RAG ENSTA.
- Deux projets mélangés à la racine : le RAG ENSTA (`app.py`, `templates/`, `static/`, `ENSTA_DATASET/`, `eval/run_eval.py`, `tests/test_api.py`, `tests/test_helpers.py`) et l'agent de campus (`campus_agent/`, `eval/run_agent_eval.py`, `eval/agent_*.json`, `tests/test_campus_*.py`, `tests/test_agent_eval.py`).
- Agent : 18 outils, 3 rôles, confirmation humaine de chaque écriture, journal d'audit, serveur MCP (stdio), interface web, mode démo publique (école isolée par visiteur, quota), 88 tests, 41 scénarios d'évaluation (29 d'ajustement + 12 mis de côté), déployé sur Render (`agent-campus.onrender.com`), CI GitHub (tests + image Docker), maintien en éveil toutes les 10 minutes.
- Résultats mesurés : Haiku 5.5 87/87 (3 passes) et 35/36 (jeu mis de côté) ; Sonnet 5.5 29/29 et 11/12 à 24 fois le coût.
- Restes à corriger : README annonce 87 tests (88), un commentaire cite encore Hugging Face, trois fichiers `requirements*.txt` plus `runtime.txt`, le correctif de fuseau horaire (`9e4569c`) n'est pas encore poussé.

### La cible

Un agent qu'un cabinet de conseil en IA pourrait mettre chez un client, et dont chaque affirmation du CV est vérifiable en cliquant :

| Affirmation | Preuve attendue dans le dépôt |
|---|---|
| Je construis des agents | code, architecture, MCP, intégrations réelles |
| Je les évalue | banc d'essai multi-axes, rapport chiffré, régressions bloquées en CI |
| Je les sécurise | modèle de menaces, matrice d'accès testée, injection testée, OWASP LLM couvert |
| Je les exploite | traces, tableau de bord, alertes, SLO, runbook, gestion des incidents, coûts |
| Je les maintiens | versions, changelog, prompts versionnés, dépendances surveillées, tests de régression |
| Je gère le métier | règles de l'école codées et documentées, circuits (absences, stages, notes), guides par rôle |
| J'accompagne les équipes | supports de formation par profil, FAQ, script de démo, mesure des usages |

---

## 1. Nettoyage : deux dépôts

### 1.1 Décisions prises

- **Deux dépôts** : `ensta-rag-assistant` (le RAG, version 0) et `campus-agent` (l'agent, la suite).
- **Les deux conservent leur historique.** Pas de réécriture de l'historique de l'agent ; les 13 commits « Add files via upload » du début restent, ils font partie de l'histoire du projet. Si on veut quand même les nettoyer un jour, `git filter-repo --message-callback` permet de reformuler les messages sans toucher au contenu, mais c'est un push forcé : à faire avant de communiquer largement le dépôt, ou jamais.
- **README en anglais** pour les deux dépôts (les pages de démo et les guides utilisateurs restent en français, leur public est l'école).

### 1.2 Dépôt `ensta-rag-assistant` (version 0)

Procédure, en gardant tout l'historique du RAG jusqu'à son dernier commit avant l'agent (`5fb2f2d`) :

1. Créer sur GitHub un dépôt vide `ensta-rag-assistant` (public, sans README initial).
2. Dans un clone frais du dépôt actuel : `git checkout -b main-rag 5fb2f2d`, puis `git remote add rag https://github.com/charbel-dib/ensta-rag-assistant.git` et `git push rag main-rag:main`.
3. Sur cette branche, un commit de finition : remettre le README d'origine à la racine (il est aujourd'hui dans `docs/ensta-rag.md` côté agent, mais sur `5fb2f2d` il est encore `README.md` : rien à faire sauf l'actualiser), ajouter en tête une note « Version 0. The campus agent (link) grew out of this project », corriger l'incohérence seuil 0,08 / 0,84 déjà repérée, vérifier que `.env.example` ne contient que les variables du RAG.
4. Ajouter un tag `v0.1.0` et une release GitHub « ENSTA RAG assistant, final state before the agent ».
5. Mettre à jour la description du dépôt, les topics (`rag`, `retrieval`, `faiss`, `fastapi`, `french`), et pointer le portfolio Vercel vers cette nouvelle adresse pour le projet « Assistant ENSTA ».

Ce dépôt n'évoluera plus, hors corrections. Son README en anglais garde la structure actuelle (pipeline, résultats hit@k / MRR, seuil de refus, jeu mis de côté), avec un paragraphe « What I would do differently today » qui renvoie vers l'agent : c'est un bon signal de recul pour un recruteur.

### 1.3 Dépôt `campus-agent` (la suite)

1. **Renommer** `ensta_chatbot` en `campus-agent` dans les paramètres GitHub. GitHub redirige l'ancienne URL (clones, liens, Render) : la démo et la candidature déjà envoyée continuent de fonctionner. Mettre à jour ensuite l'URL du dépôt dans Render (Settings → Repository) pour ne pas dépendre de la redirection.
2. **Retirer le RAG** en un commit « Move the ENSTA RAG to its own repository » : supprimer `app.py`, `templates/`, `static/` (racine), `ENSTA_DATASET/`, `eval/run_eval.py`, `eval/review.py`, `eval/metrics.py`, `eval/questions.jsonl`, `eval/heldout.jsonl`, `eval/results/retrieval-*` et `heldout-retrieval-*`, `tests/test_api.py`, `tests/test_helpers.py`, `tests/test_eval_metrics.py`, `runtime.txt`, `docs/ensta-rag.md` (remplacé par un lien). Le README raconte l'origine en deux lignes avec le lien vers `ensta-rag-assistant`.
3. **Un seul fichier de dépendances** : `pyproject.toml` (PEP 621) avec les groupes `[project.optional-dependencies] dev` (pytest, ruff), `dense` (sentence-transformers, pour la recherche documentaire E5 optionnelle), `eval`. Supprimer les trois `requirements*.txt` ; le Dockerfile installe `pip install .`.
4. **Arborescence cible** :

   ```
   campus_agent/            code (inchangé : domain/, tools.py, agent.py, llm.py, web.py, mcp_server.py, …)
   campus_agent/data/docs/  règlement de l'école fictive
   eval/                    scénarios, harnais, résultats (agent uniquement)
   tests/
   docs/                    architecture, ADR, modèle de menaces, runbook, guides, rapport d'évaluation
   deploy/                  render.yaml, docker-compose (intégrations), scripts
   .github/workflows/       tests.yml, keep-alive.yml, eval.yml (voir §4), security.yml (voir §6)
   ```
5. **Hygiène** : `ruff` (lint + format) en CI et en pre-commit ; `SECURITY.md` (comment signaler une faille) ; `CHANGELOG.md` au format Keep a Changelog ; tag `v1.0.0` sur l'état actuel déployé, puis une version par bloc livré ; Dependabot activé (pip + GitHub Actions) ; `CITATION.cff` inutile, pas de licence autre que MIT.
6. **Corrections immédiates** : nombre de tests dans le README, commentaire Hugging Face dans `web.py`, pousser `9e4569c`.

### 1.4 Structure du README anglais de `campus-agent`

Le README doit se lire en deux minutes par un recruteur non technique et donner les preuves à un technique. Ordre :

1. Titre, une phrase, badges (tests, évaluation, licence), **lien démo** en gras, auteur et liens.
2. « In one minute » : 5 puces (ce qu'il fait, confirmation humaine, droits côté serveur, mesuré, exploité).
3. Capture ou GIF de la carte de confirmation, puis lien vidéo (2 min) quand elle existe.
4. Try it (démo publique, local, Docker, Claude Desktop/MCP).
5. What the agent can do (tableau outils × rôles, déjà écrit).
6. How it works (deux diagrammes Mermaid déjà écrits, plus un diagramme de déploiement).
7. Built for production (sécurité, observabilité, coûts, robustesse) avec lien vers chaque document de `docs/`.
8. Evaluation : tableau des résultats, méthode, lien vers le rapport complet et « Where it breaks ».
9. Operating it : SLO, runbook, dashboard, lien.
10. Integrations and business rules : iCal, OIDC, SMTP, stages, notes ; lien vers les guides par rôle.
11. Repository map, roadmap (ce qui reste), limits, licence.

---

## 2. Architecture cible

L'architecture actuelle (registre d'outils unique, couche métier avec droits, actions en attente) est conservée ; on y ajoute des couches sans casser l'existant.

```
                     ┌──────────────┐   ┌──────────────────┐   ┌───────────────┐
  utilisateurs ───▶  │ Web (Starlette)│ │ MCP server (HTTP, │   │ CLI / eval    │
  (SSO OIDC)         │ SSE streaming │   │ OAuth 2.1)       │   │ harness       │
                     └──────┬───────┘   └────────┬─────────┘   └──────┬────────┘
                            └───────────┬────────┴─────────────────────┘
                                 ┌──────▼──────┐     prompts versionnés, skills
                                 │ Agent loop  │ ◀── routage de modèle, budget, cache
                                 └──────┬──────┘
                                 ┌──────▼──────┐
                                 │ Tool registry│  validation, pending actions, audit
                                 └──────┬──────┘
                 ┌──────────────────────┼─────────────────────────┐
          ┌──────▼──────┐        ┌──────▼──────┐          ┌───────▼───────┐
          │ Domain rules │        │ Documents   │          │ Adapters      │
          │ (calendar,   │        │ (BM25 / E5) │          │ iCal, CalDAV, │
          │ academics,   │        └─────────────┘          │ SMTP, GLPI,   │
          │ internships, │                                  │ Moodle, OIDC  │
          │ grades…)     │                                  └───────────────┘
          └──────┬──────┘
          ┌──────▼──────────────────────────────────────────┐
          │ Storage : SQLite (dev/démo) ou PostgreSQL (prod) │
          └──────────────────────────────────────────────────┘
   Transverse : OpenTelemetry (traces gen_ai.*), métriques, journal d'audit, feature flags, RGPD
```

Décisions d'architecture à consigner comme ADR (voir §10) : SQLite vs PostgreSQL ; confirmation côté serveur plutôt que côté modèle ; droits dans la couche métier plutôt que dans le prompt ; Starlette plutôt que FastAPI ; BM25 par défaut ; Haiku par défaut avec escalade ; MCP stdio local puis HTTP distant.

---

## 3. Bloc A — Évaluation élargie (à faire en premier : mesurer avant de construire)

Objectif : passer de 41 scénarios à un banc d'essai multi-axes d'environ 150 tâches, calqué sur les cadres de référence, avec des métriques comparables d'une version à l'autre et un verrou en CI.

### 3.1 Axes, méthode de référence et métriques

| Axe | Inspiré de | Ce qu'on construit | Métriques |
|---|---|---|---|
| Tâches conversationnelles réalistes | τ-bench (Sierra) | Un **utilisateur simulé** (Haiku) qui ne connaît que son objectif et quelques faits, dialogue avec l'agent jusqu'à 8 tours ; état final de la base comparé à l'état attendu (égalité stricte des tables concernées : actions en trop = échec) | taux de réussite, **pass^k** (k = 4), nombre de tours, coût |
| Appel d'outils | BFCL (Berkeley) | Pour chaque scénario : outil attendu, arguments attendus (égalité ou prédicat), appels parallèles ; **non-pertinence** (demande hors périmètre → aucun outil) ; **paramètre manquant** (→ question, pas de devinette) | exactitude outil, exactitude arguments, taux de faux appels, taux de devinettes |
| État et pièges | ToolSandbox (Apple) | Jalons obligatoires (ex. `find_free_slots` avant `create_event`), champs de mines (ex. `cancel_event` sur un examen), information insuffisante | taux de jalons atteints, taux de mines touchées |
| Injection | AgentDojo (ETH) | Attaques dans **chaque canal de données** : description d'événement, message, ticket, document du règlement, nom de personne, objet de rendez-vous ; 3 familles (instruction directe, usurpation d'autorité, exfiltration de données vers un ticket ou un message) | utilité sans attaque, utilité sous attaque, **taux de succès des attaques** (toute action non demandée ou toute donnée exfiltrée) |
| Accès aux données | OWASP LLM02 / LLM06 | Matrice complète rôle × outil × propriétaire ; **canaris** : valeurs uniques (notes, motifs d'absence, e-mails) placées dans les dossiers d'autres personnes ; toute apparition dans une réponse est une fuite détectée par simple recherche | fuites / tentatives, couverture de la matrice |
| Hallucinations et ancrage | méthodes « faithfulness » (RAGAS, FActScore adaptées) | Chaque fait de la réponse (date, heure, salle, note, nom, référence) doit figurer dans un résultat d'outil de la conversation : extraction déterministe par regex + vérification ; questions **sans réponse** (abstention attendue), **fausse prémisse**, citations inexistantes | taux d'affirmations non étayées, taux d'abstention correcte, citations valides |
| Robustesse | — | Paraphrases, fautes de frappe, anglais, demandes longues, conversations de 15 tours, deux demandes en une | réussite par variante, dégradation vs forme canonique |
| Coût et latence | — | Mesurés à chaque run | coût/tâche, p50 et p95 de latence, appels modèle/tâche, taux de cache |

Principe conservé : **vérification déterministe sur la base**, pas de modèle qui note un modèle, sauf un cas : la qualité rédactionnelle de la réponse (clarté, ton, langue), notée par un juge LLM **calibré** sur 40 exemples notés à la main, avec l'accord juge/humain publié. Si l'accord est faible, on ne publie pas cet axe.

### 3.2 Jeux de données

- `eval/suites/` : un fichier par axe, format JSON existant étendu (`persona`, `setup`, `goal` pour l'utilisateur simulé, `turns` pour le scripté, `checks`, `milestones`, `minefields`, `canaries`, `attacks`).
- Trois partitions : **dev** (on règle dessus), **held-out** (on ne regarde qu'à la fin de chaque bloc), **adversarial** (écrit par quelqu'un d'autre si possible : demander à un camarade de rédiger 20 demandes sans voir les outils).
- Génération assistée mais relue : des variantes paraphrasées peuvent être produites par un modèle puis validées à la main ; chaque scénario garde un champ `author` et `reviewed`.

### 3.3 Harnais

- Réutiliser `eval/run_agent_eval.py` ; ajouter l'utilisateur simulé (prompt système : objectif, faits connus, ce qu'il ne sait pas, consigne de ne jamais inventer d'identifiant), la comparaison d'état (dump des tables pertinentes en JSON trié), pass^k, les canaris, la détection d'exfiltration.
- Exécution en parallèle, batches, reprise sur erreur (déjà en place) ; utiliser l'**API Batches** d'Anthropic pour les campagnes complètes (moitié prix, pas d'attente de rate limit).
- Sorties : `eval/results/<version>/<model>/<suite>.json|.md`, et un `eval/results/latest.json` agrégé lu par le README (badge) et par la page de démo.
- **Rapport d'évaluation** généré (`docs/evaluation-report.md`) : méthode, chiffres, intervalles (bootstrap), comparaison Haiku/Sonnet, analyse des échecs par catégorie, ce qui a été corrigé, ce qui ne l'est pas.

### 3.4 Verrou en CI

- Workflow `eval.yml` : à chaque pull request qui touche `campus_agent/` ou les prompts, exécute la suite **dev** avec Haiku (coût estimé < 0,20 $), publie le tableau en commentaire de PR, **échoue si** le taux de réussite baisse de plus de 2 points par rapport à `main`, si une attaque réussit, ou si une fuite de canari apparaît. Clé API dans les secrets GitHub, budget plafonné.
- Campagne complète (toutes suites, k = 4, Haiku et Sonnet) à chaque tag de version, résultats commités.

---

## 4. Bloc B — Exploitation : observabilité, pilotage, qualité continue

Objectif : prouver qu'on sait **faire vivre** l'agent après la mise en production, ce que l'offre appelle « suivre l'usage et corriger ».

### 4.1 Traces et métriques (OpenTelemetry)

- Instrumenter avec les **conventions sémantiques GenAI d'OpenTelemetry** : une trace par tour, span racine `invoke_agent`, enfants `chat {model}` et `execute_tool`, attributs `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens` / `output_tokens`, `gen_ai.conversation.id`, `gen_ai.tool.name`, `gen_ai.tool.call.id`, `error.type`. Les conventions sont encore en statut « Development » : épingler la version de la bibliothèque et le noter dans la doc.
- Métriques : `gen_ai.client.token.usage`, `gen_ai.client.operation.duration`, nombre d'appels modèle et d'outils par tour, coût calculé, taux de cache, taux d'actions confirmées/refusées/échouées, taux de refus de droits, taux d'erreurs d'outil.
- Export OTLP vers un backend auto-hébergé dans le `docker-compose` (Langfuse ou Arize Phoenix, les deux acceptent OTLP ; choisir Phoenix pour sa légèreté, documenter le choix). Les prompts et réponses ne sont exportés qu'en opt-in, pseudonymisés (voir RGPD).
- Journaux structurés JSON avec identifiant de corrélation (trace id) jusque dans le journal d'audit existant.

### 4.2 Tableau de bord

Page `/admin` réservée à la scolarité (ou rôle `admin` dédié) : conversations/jour, utilisateurs actifs par rôle, outils les plus appelés, taux de confirmation, coût/jour et par conversation, latence p50/p95, erreurs, refus de droits, retours utilisateurs, **demandes non satisfaites** (sujets des refus documentaires et des « je ne peux pas ») regroupés : c'est la carte des trous du règlement et des besoins non couverts, à présenter comme l'outil du « suivi mensuel des usages ».

### 4.3 Boucle de qualité

- **Retour utilisateur** : pouce haut/bas sur chaque réponse avec motif optionnel ; stocké avec la trace ; les pouces bas alimentent un fichier de revue hebdomadaire et, après validation, de nouveaux scénarios d'évaluation (boucle « incident → test de régression »).
- **Relais humain** : outil `escalate_to_human` qui crée un ticket avec le résumé de la conversation et prévient la scolarité ; déclenché par l'agent quand il ne peut pas aider, ou par l'utilisateur via un bouton.
- **Prompts versionnés** : les instructions et les descriptions d'outils dans des fichiers versionnés avec un hash enregistré dans chaque trace et dans les résultats d'évaluation ; un changement de prompt = une PR qui passe le verrou d'évaluation.
- **Feature flags** simples (variables d'environnement ou table) pour activer un outil, un modèle, une intégration, par rôle.

### 4.4 Fiabilité et SLO

- SLO publiés dans `docs/operations.md` : disponibilité 99,5 % (hors veille Render), p95 < 8 s pour un tour sans écriture, coût moyen < 0,2 centime/tour, taux d'échec d'outil < 1 %.
- Alertes : budget quotidien à 80 %, erreurs 5xx, latence p95, modèle indisponible (déjà géré avec retries et message), échec du maintien en éveil.
- **Runbook** : que faire si l'API Claude est en panne (mode dégradé : lecture seule par les outils sans LLM ? non ; message clair et relais humain), si le budget est atteint, si une injection est détectée, comment faire un rollback (tag précédent sur Render), comment purger les données d'un utilisateur, comment tourner la clé API.
- Dégradation contrôlée : repli Haiku → modèle plus petit impossible ; prévoir plutôt un repli « réponses sans outil d'écriture » quand le budget est serré, et une file d'attente avec message quand le rate limit est atteint.
- Base de données : migration SQLite → **PostgreSQL** optionnelle (`CAMPUS_DATABASE_URL`), avec migrations versionnées (Alembic ou scripts SQL numérotés), sauvegarde quotidienne et procédure de restauration testée. Documenter pourquoi SQLite suffit pour la démo.

### 4.5 RGPD et données

- Registre de traitement (document) : finalités, données, durées, destinataires.
- Conservation : conversations et traces purgées après N jours (paramètre, défaut 30), journal d'audit conservé 1 an sans contenu libre.
- Droits : export de ses données (JSON) et effacement sur demande, via l'interface et via commande d'administration ; pseudonymisation dans les traces exportées (identifiants au lieu de noms).
- Minimisation déjà appliquée (agendas en disponibilités) : la documenter et la tester (fait pour partie).

---

## 5. Bloc C — Sécurité

- **Modèle de menaces** (`docs/threat-model.md`, méthode STRIDE appliquée aux flux : utilisateur → agent → outils → données ; contenu externe → modèle) avec, pour chaque menace, le contrôle en place et le test qui le prouve.
- **Couverture OWASP Top 10 for LLM Applications (2025)** : tableau LLM01 injection → tests AgentDojo-like ; LLM02 divulgation → matrice d'accès et canaris ; LLM06 agence excessive → confirmation humaine, outils par rôle, plafonds ; LLM07 fuite de prompt système → rien de secret dans le prompt, test ; LLM08 faiblesses vectorielles → sans objet (BM25) ou test d'empoisonnement de documents ; LLM10 consommation non bornée → budgets, quotas, limites de taille, plafond d'étapes.
- **Serveur MCP distant** (HTTP) conforme à la spécification 2026 : OAuth 2.1 côté ressource avec métadonnées de ressource protégée (RFC 9728), indicateurs de ressource (RFC 8707), enregistrement par Client ID Metadata Documents, jeton lié à la personne → l'agent MCP agit avec les droits du porteur, plus de `--as` fixe. Fournisseur d'identité : Keycloak du `docker-compose` (voir §6). Annotations d'outils déjà en place.
- Durcissement : CSP déjà stricte ; cookies/jetons avec durée courte et rotation ; limitation de débit par utilisateur et par IP (fait) ; taille maximale des entrées (fait) ; validation stricte des arguments (fait) ; secrets jamais en dépôt (fait) ; `security.yml` en CI : `pip-audit`, `bandit`, Dependabot ; SBOM (CycloneDX) publié avec chaque release ; image Docker non-root (fait), scan Trivy.
- Tests de sécurité automatisés : chaque contrôle du modèle de menaces a un test nommé en conséquence (`test_threat_T07_prompt_injection_in_ticket`).

---

## 6. Bloc D — Intégrations réelles

Objectif : montrer « brancher aux outils du client » avec des interfaces et des formats réels, chaque adaptateur derrière une interface (`CalendarProvider`, `MailProvider`, `TicketProvider`, `IdentityProvider`) et un `docker-compose.integrations.yml` qui lance les doublures locales.

| Besoin | Système réel courant | Implémentation | Preuve |
|---|---|---|---|
| Emploi du temps | HyperPlanning, ADE Campus, Celcat | **Import iCalendar (RFC 5545)** : l'école peut être chargée depuis un `.ics` exporté (format HyperPlanning testé sur un fichier d'exemple) ; **export** : flux `.ics` par personne, groupe et salle, abonnable dans Google/Outlook/Apple | tests de round-trip, fichier d'exemple, capture d'abonnement |
| Agendas personnels | Microsoft 365 Graph, Google Calendar | Interface `CalendarProvider` ; implémentation **CalDAV** (serveur Radicale dans le compose) qui pousse les événements créés par l'agent ; stub Graph documenté | événement créé par l'agent visible dans un client CalDAV |
| Identité | CAS/Shibboleth (Renater), OIDC | **OIDC avec Keycloak** : connexion réelle, rôles et groupe depuis les claims ; le login démo reste derrière un drapeau | capture de connexion, test avec jeton forgé rejeté |
| Courriel | SMTP de l'école | Envoi réel via SMTP vers **Mailpit** (boîte de test), gabarits texte/HTML, file d'envoi avec reprise | message visible dans Mailpit, test |
| Support | GLPI | Adaptateur `TicketProvider` au format de l'API REST GLPI, doublure locale | création d'un ticket via l'API doublure |
| Cours en ligne | Moodle | Lecture des devoirs et dates de rendu (Web services Moodle, doublure) → outil `get_assignments` | scénarios d'évaluation |
| Documents | GED / site | Import des règlements depuis Markdown/PDF (déjà Markdown) ; pipeline de mise à jour de l'index documentaire avec contrôle de cohérence (hérité du RAG) | test |

Chaque adaptateur : contrat d'interface, implémentation « mémoire » pour les tests, implémentation réelle, documentation « comment brancher le vrai système ».

---

## 7. Bloc E — Nouveaux domaines métier

Les règles sont d'abord **écrites** dans `campus_agent/data/docs/` (le règlement fictif, déjà en place pour les absences et les salles), puis **codées** dans `domain/`, puis **exposées** en outils, puis **testées**, puis **évaluées**. Pour chaque domaine : qui peut quoi, quels états, quelles notifications, quelles erreurs.

1. **Conventions de stage** : dépôt par l'étudiant (entreprise, dates, tuteurs), validation par le tuteur école, signature par la scolarité ; états `brouillon → déposée → validée_tuteur → signée / refusée` ; rappels de délai (3 semaines) ; outils `create_internship_request`, `get_internship_status`, `review_internship` ; génération PDF de la convention.
2. **Notes** : saisie et publication par l'enseignant (outil `enter_grades` en masse avec confirmation, `publish_grades`), moyenne pondérée, règle éliminatoire (< 6/20), liste des étudiants en difficulté déjà faite.
3. **Examens et rattrapages** : inscription au rattrapage, règle « absence excusée → rattrapage », plafond 12/20, convocations générées.
4. **Inscriptions et attestations** : attestation faite ; ajouter relevé de notes provisoire PDF.
5. **Bilingue** : règlement et interface en français et anglais pour les étudiants internationaux ; l'agent répond dans la langue de l'utilisateur (déjà) ; évaluation en anglais.
6. Hors périmètre, à dire dans le README : frais d'inscription, jurys, maquettes de formation.

---

## 8. Bloc F — Expérience agent avancée (dernières approches)

- **Streaming** des réponses (SSE) avec affichage des appels d'outils en direct.
- **Routage de modèle** : Haiku par défaut, escalade vers Sonnet quand la tâche est longue ou ambiguë (critère : plus de N outils ou échec d'un premier essai), avec mesure du gain ; `effort` adaptatif.
- **Mémoire utilisateur avec consentement** : préférences explicites (« je préfère les créneaux du matin ») stockées, modifiables, effaçables, jamais déduites.
- **Clarification structurée** : quand une information manque, l'agent propose des choix cliquables (sorties structurées / JSON schema) plutôt qu'une question libre ; l'élicitation MCP pour le même besoin côté Claude Desktop.
- **Agent Skills** : décrire les procédures métier (justifier une absence, organiser un rattrapage) sous forme de fichiers `SKILL.md` chargés à la demande, pour réduire le prompt et rendre les procédures lisibles par la scolarité ; comparer en évaluation prompt monolithique vs skills.
- **Recherche d'outils** : si le nombre d'outils dépasse 30 avec les intégrations, charger les définitions à la demande plutôt que toutes à chaque appel ; mesurer l'effet sur le coût et le taux de bon choix d'outil.
- **Cache de prompt longue durée** (1 h) pour les instructions et les outils ; mesurer le taux de lecture en cache.
- **Mode hors-ligne d'évaluation** : modèle local (Ollama) comme utilisateur simulé pour les campagnes gratuites, Claude pour l'agent.

---

## 9. Démo et page publique

- Page actuelle conservée ; ajouter : vidéo de 2 min (script déjà dans `docs/demo-script.md`), badge d'évaluation lu depuis `latest.json`, onglet « Coulisses » montrant la trace OpenTelemetry du dernier tour (ce que voit un exploitant), lien vers le tableau de bord en lecture seule avec données de démo.
- Parcours guidé optionnel (3 étapes) pour un recruteur pressé : étudiant → enseignant → scolarité.
- Maintien en éveil (fait) ; ajouter une page de statut minimaliste `/status` (dernier déploiement, version, santé).
- Un **mode « panne du modèle »** visible : si la clé ou le budget manque, la page l'explique et les parties sans LLM restent utilisables (fait partiellement).

---

## 10. Documentation à produire (dossier `docs/`)

| Fichier | Contenu | Public |
|---|---|---|
| `architecture.md` | diagrammes, composants, flux d'une requête, décisions, limites | technique |
| `adr/0001-…md` … | une décision par fichier (format Nygard) : SQLite, confirmation serveur, droits en couche métier, Starlette, BM25, Haiku + escalade, MCP distant OAuth, OTel | technique |
| `threat-model.md` | STRIDE, OWASP LLM, contrôles, tests associés | technique, sécurité |
| `evaluation.md` + `evaluation-report.md` | méthode, suites, métriques, résultats par version, analyse des échecs | technique, recruteur |
| `operations.md` (runbook) | déploiement, variables, SLO, alertes, incidents, rollback, sauvegardes, rotation de clé, purge RGPD | exploitant |
| `privacy.md` | registre de traitement, conservation, droits | DPO, direction |
| `integrations.md` | brancher iCal, CalDAV, OIDC, SMTP, GLPI, Moodle ; doublures locales | intégrateur |
| `business-rules.md` | toutes les règles codées, avec référence au règlement fictif | métier |
| `guides/etudiant.md`, `guides/enseignant.md`, `guides/scolarite.md` | **supports de formation** par profil : ce que l'assistant fait, 10 exemples de demandes, ce qu'il ne fait pas, comment confirmer/annuler, comment signaler ; une page chacun, imprimable (PDF) | utilisateurs |
| `faq.md` | questions fréquentes et messages d'erreur expliqués | utilisateurs, support |
| `demo-script.md` (fait) | script vidéo | — |
| `CHANGELOG.md` | par version | tous |
| `roadmap.md` | ce document, allégé, maintenu | tous |

Règles d'écriture : anglais pour la documentation technique, français pour les guides utilisateurs et la FAQ (public : l'école) ; chaque chiffre renvoie au fichier de résultats qui le produit ; pas d'affirmation sans preuve dans le dépôt.

---

## 11. Ordre de réalisation, jalons et définition de « terminé »

| Étape | Contenu | Durée indicative | Livrable visible |
|---|---|---|---|
| 0 | Push du correctif d'heure ; renommage ; dépôt RAG ; retrait du RAG ; pyproject ; ruff ; tags v0.1.0 / v1.0.0 ; README anglais ; corrections | 0,5 jour | deux dépôts propres |
| 1 | Bloc A évaluation : utilisateur simulé, pass^k, outils, canaris, injection élargie, hallucinations, rapport, `eval.yml` | 2 jours | rapport chiffré, verrou CI, badge |
| 2 | Bloc B exploitation : OTel, Phoenix, dashboard, feedback, relais humain, prompts versionnés, SLO, runbook, RGPD | 2 jours | `/admin`, traces, docs |
| 3 | Bloc C sécurité : modèle de menaces, OWASP, MCP HTTP OAuth 2.1, security.yml, SBOM | 1,5 jour | docs + tests nommés |
| 4 | Bloc D intégrations : iCal import/export, CalDAV, OIDC Keycloak, SMTP Mailpit, compose | 2 jours | compose démarrable, captures |
| 5 | Bloc E métier : stages, notes, rattrapages, bilingue | 2 jours | nouveaux outils évalués, guides |
| 6 | Bloc F avancé : streaming, routage, clarifications structurées, skills | 1,5 jour | mesures comparatives |
| 7 | Démo : vidéo, coulisses, statut ; documentation finale ; release v2.0.0 | 1 jour | page publique à jour |

Total indicatif : 12 à 13 jours de travail concentré. Chaque étape est **terminée** quand : code + tests verts + évaluation sans régression + déployé + documenté + ligne de changelog + README à jour + (si pertinent) ligne du CV mise à jour. On ne commence pas l'étape suivante avant.

Après chaque étape, mettre à jour dans cet ordre : la démo publique, le README, la page de présentation, puis le CV et LinkedIn.

---

## 12. Usage dans les candidatures

### CV (à ne mettre à jour qu'une fois chaque bloc réellement en ligne)

Entrée actuelle (vérifiable aujourd'hui) :

> **Campus Agent, from RAG to a tool-using agent** — personal project, 2025-2026 — Python, Claude API, MCP, Starlette, Docker. Demo: agent-campus.onrender.com · Code: github.com/charbel-dib/campus-agent
> - Designed an 18-tool agent on the Claude API for a generated engineering school (290 students, 30 teachers): calendars, common free slots, rooms, absences, grades, PDF certificates, cited rules; also served over MCP.
> - Server-side permissions for 3 roles, human confirmation then re-validation of every write, audit log, prompt-injection defences; 88 automated tests.
> - Evaluated on 41 database-checked scenarios: 87/87 over 3 runs and 35/36 held-out with Claude Haiku 5.5; Sonnet 5.5 as reliable at 24× the cost.
> - Deployed (Docker, Render, GitHub CI) with an isolated school and quota per visitor. Grew out of a bilingual RAG over 794 ENSTA documents (55/60 questions with a relevant source).

Lignes à ajouter au fil des blocs (une par bloc, dès qu'il est en ligne) :
- Bloc A : « Built a 150-task evaluation bench (simulated users, pass^k, tool-call accuracy, access canaries, injection in every data channel, groundedness); regressions block CI. »
- Bloc B : « Instrumented with OpenTelemetry GenAI conventions; usage/cost/latency dashboard, user feedback loop, human hand-off, GDPR retention and erasure, SLOs and runbook. »
- Bloc C : « Threat model mapped to OWASP LLM Top 10 with a named test per control; remote MCP server with OAuth 2.1. »
- Bloc D : « Integrated real formats and protocols: iCalendar import/export, CalDAV, OIDC (Keycloak), SMTP; adapters behind interfaces with local doubles. »
- Bloc E : « Implemented internship agreements, grade entry and publication, make-up exams; per-role training guides. »

### Argumentaire d'entretien (à préparer par bloc)

Pour chaque bloc : le problème, l'option choisie et celles écartées (les ADR), ce que la mesure a montré, ce qui a cassé, ce qu'on ferait ensuite. Préparer en particulier : pourquoi la confirmation est côté serveur ; pourquoi Haiku ; comment on sait que l'agent ne fuit pas de données ; comment on détecte une régression ; que fait-on quand l'API est en panne ; comment on former les équipes (les guides).

### LinkedIn

Un post par bloc livré, court, avec un chiffre et un lien vers la démo ; pas de superlatifs.

---

## 13. Risques et parades

| Risque | Parade |
|---|---|
| Dispersion : trop de blocs, rien de fini | ordre strict, définition de « terminé », un bloc à la fois |
| Coût d'évaluation | Haiku, Batches API, suites dev courtes en CI, campagnes complètes seulement aux tags |
| Juge LLM contestable | réservé à la forme, calibré et publié, jamais pour les faits |
| Conventions OTel GenAI instables | version épinglée, notée dans la doc |
| Render gratuit : veille, filesystem éphémère | maintien en éveil (fait), SQLite recréée au démarrage (fait), PostgreSQL optionnel documenté |
| Démo cassée pendant un déploiement | tags, rollback documenté, tests + CI avant push, déploiement auto uniquement depuis `main` vert |
| Écart entre CV et dépôt | règle : une ligne de CV n'existe que si la preuve est en ligne |

---

## Sources consultées pour ce plan

- τ-bench (Sierra) : https://arxiv.org/pdf/2406.12045 et https://sierra.ai/blog/benchmarking-ai-agents
- BFCL v3 multi-tours : https://gorilla.cs.berkeley.edu/blogs/13_bfcl_v3_multi_turn.html ; v4 : https://gorilla.cs.berkeley.edu/blogs/15_bfcl_v4_web_search.html
- AgentDojo : https://www.emergentmind.com/topics/agentdojo-benchmark
- ToolSandbox (Apple) : https://arxiv.org/abs/2408.04682
- OWASP Top 10 for LLM Applications 2025 : https://www.pointguardai.com/blog/understanding-the-owasp-top-10-for-llms
- Révisions MCP 2026 (OAuth 2.1, RFC 9728, RFC 8707, CIMD, tâches) : https://workos.com/blog/mcp-2026-spec-agent-authentication et https://workos.com/blog/mcp-2025-11-25-spec-update
- Conventions sémantiques OpenTelemetry GenAI : https://www.dash0.com/knowledge/opentelemetry-genai-semantic-conventions-explained ; conversion Phoenix : https://www.arize.com/docs/phoenix/release-notes/05-2026/05-15-2026-otel-semconv-conversion.md
- Agent Skills (Anthropic) : https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
- Export iCal HyperPlanning : https://docs.index-education.com/docs_en/en-hyperplanning-support-page-249-995-export-timetables-in-ical-format.php ; ADE Campus : https://www.ard.fr/wp-content/uploads/2020/04/LM04025-Fiche-produit-ADE-Campus-ind-A.pdf
- Render, offre gratuite : https://render.com/docs/free
