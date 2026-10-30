# Interactive Formal Verification Tool

Interactive Formal Verification Tool est un projet open source visant à aider les informaticiens, les chercheurs et les agents fondés sur des LLM à investiguer les situations problématiques d'un logiciel ou d'un raisonnement textuel. Il vise à identifier les propriétés à établir, les hypothèses et les informations manquantes, puis à rechercher des éléments vérifiables pour étayer ou réfuter ces propriétés.

Le projet ne promet ni de découvrir toutes les défaillances, ni de démontrer automatiquement la correction d'un système. Sa valeur ajoutée est une **investigation interactive pilotée par les incertitudes** : le LLM détermine ce qu'il ne sait pas, recherche les informations disponibles, interroge l'utilisateur lorsque c'est nécessaire et transforme progressivement les réponses en propriétés et obligations de preuve vérifiables.

**État du projet :** une première tranche expérimentale analyse statiquement les fonctions Python de premier niveau, enregistre les réponses à des questions et permet de proposer puis d'accepter explicitement des propriétés. Elle peut aussi générer des propositions avec le modèle local Ollama `bonsai-2:4b` ou les importer depuis un fichier. Le reste des fonctionnalités, interfaces et intégrations décrites ci-dessous reste à réaliser. L'analyse de raisonnements textuels fait partie de la vision, mais son périmètre et ses méthodes restent à définir.

## Premiers pas

Depuis la racine du dépôt, avec Python 3.10 ou plus récent :

```text
python -m formal_agent start module.py investigation.json
python -m formal_agent show investigation.json
python -m formal_agent propose-ollama investigation.json
python -m formal_agent ask investigation.json
python -m formal_agent answer investigation.json U1
python -m formal_agent add-property investigation.json U1 --statement "La propriété attendue"
python -m formal_agent add-assumption investigation.json U1 P1 --statement "Hypothèse à vérifier"
python -m formal_agent accept-property investigation.json P1
python -m formal_agent show investigation.json
python -m formal_agent report investigation.json
python -m unittest discover -s tests
```

La commande `answer` affiche la question puis demande une réponse ; `--answer "..."` permet aussi de fournir celle-ci directement. `start` refuse d'écraser un fichier existant. L'état JSON est enregistré localement ; il contient notamment le chemin et l'empreinte SHA-256 du fichier analysé, les observations et les réponses. Protégez ce fichier s'il contient des informations sensibles.

Pour consulter des références lors de l'investigation, ajoutez au démarrage une option `--evidence` par fichier, par exemple `python -m formal_agent start module.py investigation.json --evidence exigences.txt --evidence test_module.py`. Les fichiers doivent être en UTF-8 et ne pas dépasser 32 768 octets chacun. Ils sont lus comme du texte non fiable, jamais exécutés, et leurs chemins et empreintes sont conservés dans le rapport. Si une référence change ou disparaît, l'investigation reste consultable mais les modifications sont bloquées ; il faut en créer une nouvelle.

L'option facultative `--discover-evidence` recherche uniquement des fichiers conventionnels proches de la source : `README.md`, un document de même nom que le module, `test_<module>.py`, `<module>_test.py` et `test_<module>.py` dans le sous-dossier `tests`. Elle n'effectue aucune recherche récursive ; inspectez le rapport pour vérifier les fichiers retenus avant d'appeler le modèle. Une référence trouvée hors du dossier source par lien symbolique est refusée.

Lorsqu'une réponse s'appuie sur une référence déclarée, utilisez `answer investigation.json U2 --answer "réponse" --reference exigences.txt --line 1` pour enregistrer son chemin, son empreinte et la ligne citée. La citation et son interprétation restent des décisions utilisateur non vérifiées ; le système ne considère pas automatiquement le document comme une exigence confirmée.

Un exemple de fonction de retrait et de spécification se trouve dans [examples](examples/). L'évaluation de bout en bout utilise un fournisseur simulé, sans réseau, et vérifie notamment la reprise entre processus, les limites des garanties et l'absence d'exécution de la cible.

Une fois une question répondue, `add-property` enregistre une propriété candidate rédigée par l'utilisateur, liée à cette réponse. `accept-property` la retient explicitement comme spécification ; **cela ne la vérifie ni ne la démontre**. Les investigations au format initial sont migrées en mémoire à la lecture, puis enregistrées au nouveau format lors de la prochaine modification.

`link-property` relie explicitement une question à une propriété de la même fonction. Après une réponse, `add-assumption` enregistre une hypothèse formulée par l'utilisateur pour une propriété liée. Le rapport en conserve l'origine, la réponse source et la propriété concernée : une hypothèse reste non vérifiée, même si la propriété est acceptée. Une propriété issue d'une réponse est automatiquement liée à cette réponse.

L'investigation suit trois phases persistantes : `active`, `awaiting_answer` et `finished`. `ask` choisit une question ouverte en donnant priorité aux liens vers des propriétés acceptées, puis candidates ; `ask investigation.json U2` choisit explicitement une question. La réponse attendue remet l'investigation en phase active ; `resume` permet aussi d'abandonner l'attente ou de rouvrir une investigation terminée. `finish` refuse les questions ou désaccords ouverts sauf avec `--allow-open` : ceux-ci restent visibles dans le rapport et ne sont pas considérés comme résolus. Terminer l'investigation ne signifie pas que des propriétés sont prouvées.

Si deux questions sur la même fonction sont équivalentes, l'utilisateur peut enregistrer sa décision avec `mark-duplicate investigation.json U3 U2 --reason "même question"`. La question `U3` ne sera plus sélectionnée, mais `U2` conserve son propre statut ; les propriétés liées à `U3` sont également reliées à `U2`. La décision et sa raison apparaissent dans le rapport. `reopen-unknown investigation.json U3` annule le statut de doublon, sans retirer les liens ajoutés à `U2`. L'équivalence n'est pas déduite automatiquement et le système ne détecte pas tous les doublons sémantiques.

Un désaccord relevé par l'utilisateur peut être enregistré avec `record-conflict investigation.json U2 --description "description du désaccord"`, puis une décision consignée avec `resolve-conflict investigation.json C1 --resolution "décision"`. Les deux restent visibles dans le rapport ; cette décision ne vérifie ni les sources ni une propriété. La détection automatique des contradictions n'est pas encore implémentée.

`report` produit un rapport textuel déterministe sur les fonctions, imports, appels et lignes observés, les questions, réponses, propriétés, hypothèses et leurs limites ; il indique qu'aucune vérification du code cible n'a été effectuée. Les docstrings et les références sont indiquées comme non vérifiées. `show` conserve la sortie JSON complète.

### Générer des propositions avec Ollama

La commande facultative `propose-ollama` utilise `bonsai-2:4b`, qui doit déjà être installé sur un serveur Ollama accessible à `127.0.0.1:11434` depuis l'environnement où la commande s'exécute. Dans cette machine, lancer `wsl python3 -m formal_agent propose-ollama investigation.json` depuis la racine du dépôt utilise l'instance WSL. Vérifiez le modèle avec `wsl ollama list`.

La commande transmet au serveur local les extraits des fonctions de premier niveau, y compris leurs docstrings, les imports et appels syntaxiques, le contenu des fichiers de référence, ainsi que les questions, réponses, propriétés, liens, hypothèses et désaccords de l'investigation. Ne l'utilisez qu'avec un serveur local de confiance et sur des données que vous acceptez de lui transmettre : le serveur peut conserver ses propres journaux. L'adaptateur désactive les proxies et les redirections, et ne permet pas de choisir un serveur distant.

Le raisonnement étendu est désactivé pour ce modèle et la réponse est demandée sous forme JSON structurée. Les limites par défaut sont de 300 secondes, 1 400 tokens de sortie et 65 536 octets de contexte ; les options `--timeout` et `--max-tokens` permettent de changer les deux premières. Une sortie incomplète, invalide ou incompatible avec la source est signalée et n'est pas enregistrée. Chaque proposition est validée structurellement et reste candidate ou ouverte : sa pertinence, sa correction et l'absence d'injection ne sont pas garanties. Elle doit être revue par l'utilisateur avant toute acceptation.

### Importer des propositions

Un LLM ou un autre outil externe peut préparer localement un fichier JSON de propositions. La commande `python -m formal_agent import-proposals investigation.json propositions.json --reasoner "nom-du-modele"` vérifie sa structure et l'enregistre sans l'exécuter. Utilisez l'empreinte `model.sha256` et les noms et lignes de fonctions visibles avec `show` :

```json
{
  "source_sha256": "<empreinte de la source analysee>",
  "properties": [
    {"function": "withdraw", "line": 1, "statement": "Le montant est positif"}
  ],
  "unknowns": [
    {
      "function": "withdraw",
      "line": 1,
      "description": "Le traitement des montants negatifs est inconnu.",
      "question": "Les montants negatifs doivent-ils etre refuses ?",
      "impact": "Le domaine des entrees reste a preciser."
    }
  ]
}
```

Les deux tableaux sont obligatoires, mais l'un d'eux peut être vide. Toute proposition doit concerner une fonction du modèle et la même empreinte source ; un élément invalide rejette tout le lot sans modification. Les répétitions exactes sont ignorées et signalées avec leur position et l'identifiant correspondant, y compris si elles figurent plusieurs fois dans le même lot ; les autres propositions restent enregistrées. Une propriété n'est répétée que si son texte, sa fonction et son fournisseur correspondent. Pour une inconnue, la fonction, la question, la description et l'impact doivent correspondre, après suppression des espaces en début et fin. Une question identique après normalisation de la casse et des espaces, mais dont les détails diffèrent, reste ouverte et est signalée pour examen ; elle n'est pas automatiquement fusionnée, même si une question précédente a déjà reçu une réponse. Les répétitions sémantiques peuvent ne pas être détectées. Un lot ne contenant que des répétitions est signalé sans modifier l'investigation ; les signalements ne sont pas conservés dans le fichier d'état.

Les propriétés restent candidates, les inconnues restent ouvertes et leur origine est indiquée dans l'investigation. La structure valide ou le nom du fournisseur ne prouvent ni que les propositions importées viennent réellement d'un LLM ni qu'elles sont correctes : elles doivent être examinées par l'utilisateur. L'interface Python `ReasoningProvider` permet de brancher un fournisseur ; aucun appel réseau ni génération par LLM n'est effectué par l'import JSON. Ce même traitement s'applique à `propose-ollama`.

Le code, les docstrings et les réponses du fournisseur sont des données non fiables. Les instructions système demandent au modèle de ne pas suivre les instructions trouvées dans les sources ; ni cette consigne ni la validation de structure ne garantissent l'absence d'injections ou d'erreurs de raisonnement.

Une investigation est un instantané : `show` avertit si la source a changé ou n'est plus accessible, et `report` indique l'état de la source, tout en permettant de consulter l'état sauvegardé. Dans ces cas, les commandes qui modifient l'investigation refusent de l'enregistrer. Il faut créer une nouvelle investigation ; le transfert des réponses vers une nouvelle version du code n'est pas encore pris en charge. Une modification de l'état détectée pendant une commande bloque aussi sa sauvegarde ; cette vérification n'est pas un verrou transactionnel. N'exécutez pas plusieurs commandes d'écriture simultanément sur la même investigation.

Sous Windows avec WSL et Python 3, les mêmes commandes peuvent être lancées depuis la racine du dépôt en remplaçant `python` par `wsl python3`.

L'analyse utilise l'AST sans importer ni exécuter le fichier. Elle extrait les fonctions de premier niveau, leurs paramètres, les appels syntaxiques, les imports explicites au niveau du module et dans chaque fonction, ainsi que les lignes de `return` et `raise`, sans explorer les corps de fonctions ou classes imbriquées. Les imports et appels sont des observations syntaxiques : leur présence ne garantit pas leur exécution et ils ne constituent pas un graphe de dépendances résolu. L'analyse ne résout pas les appels dynamiques et ne couvre pas encore les méthodes, les scénarios, l'exécution des tests du code analysé ou les preuves formelles. Le modèle peut consulter les références fournies mais ne démontre pas qu'il les a interprétées correctement. Une réponse enregistrée ne constitue pas une spécification validée.

Les choix et prochaines étapes de cette tranche sont documentés dans [l'ADR 0001](docs/decisions/0001-first-investigation-slice.md).

## Principe : investiguer avant de conclure

Le processus visé est une boucle explicite et persistante :

**analyse → incertitude → recherche et question → formalisation → vérification → contre-exemple → raffinement**

1. Analyser l'objet étudié et construire un modèle de son fonctionnement ou de son raisonnement.
2. Proposer des propriétés, des hypothèses et des scénarios de défaillance ; identifier les informations manquantes.
3. Chercher les réponses dans les sources disponibles. Si une incertitude pertinente subsiste, poser une question ciblée à l'utilisateur et enregistrer sa décision.
4. Formaliser les propriétés retenues et les hypothèses dont elles dépendent en obligations de preuve.
5. Soumettre ces obligations à des tests ou à des moteurs de vérification indépendants.
6. Examiner les résultats et les contre-exemples, affiner le modèle et relancer les vérifications affectées.
7. Produire un rapport traçable sur les garanties obtenues, leurs conditions et les questions restantes.

L'investigation doit pouvoir être suspendue lorsqu'une réponse ou une ressource manque, puis reprise sans perdre son état. Une question doit correspondre à une incertitude concrète et avoir un effet identifiable sur une propriété ou une obligation de preuve. Sa priorité dépend notamment de son impact, du blocage éventuel d'une vérification et de la possibilité de la résoudre automatiquement.

### Exemple : une opération de retrait

Pour une fonction Python `withdraw(account, amount)`, l'agent pourrait se demander si le montant doit être strictement positif, si un retrait supérieur au solde doit être refusé, si l'opération doit être atomique ou si une erreur de persistance doit annuler le retrait. Il cherche d'abord des indices dans le code, les tests, la documentation et les dépendances.

Si le comportement attendu reste ambigu et bloque une vérification, il demande une clarification. Une réponse telle que « l'appelant garantit un montant strictement positif » peut devenir une hypothèse explicite d'un contrat. Les résultats obtenus sous cette hypothèse doivent rester **conditionnels** : ils ne démontrent pas que tous les appelants la respectent. Si un contre-exemple apparaît, le système doit en rechercher la cause plutôt que modifier silencieusement le contrat pour le faire disparaître.

Pour un raisonnement textuel, la même démarche vise à distinguer les affirmations, les prémisses, les interprétations et les conclusions, puis à expliciter les hypothèses et les points nécessitant une clarification. La formalisation proposée doit être confrontée au texte et validée avant qu'une vérification ne puisse apporter une garantie sur cette interprétation.

## Rôles et limites des mécanismes

- **Le LLM** aide à comprendre les sources, proposer des propriétés et des hypothèses, identifier les inconnues, dialoguer, analyser les contre-exemples et orchestrer les outils. Il n'est pas l'autorité qui valide ses propres conclusions.
- **Les tests, le fuzzing, l'analyse statique et la vérification formelle** fournissent des éléments de nature et de portée différentes. Un test réussi concerne les cas exécutés ; une vérification bornée reste limitée par ses bornes ; une preuve dépend du modèle, de la spécification, des hypothèses et du cadre formel déclaré.
- **Les moteurs logiques et ontologiques** peuvent représenter les connaissances et en déduire des conséquences, sans remplacer la vérification du logiciel. En particulier, dans la sémantique du monde ouvert d'OWL, l'absence d'une information n'établit pas sa négation.

Une preuve d'une propriété incorrecte, trop permissive ou sans cas admissible ne garantit pas que le système satisfait les besoins réels. La pertinence, la cohérence et la non-vacuité des spécifications doivent donc être examinées, notamment à l'aide de contre-exemples et, lorsque c'est approprié, du mutation testing.

## Architecture cible

Le projet privilégie un **monolithe modulaire** plutôt que des microservices introduits prématurément. Les responsabilités envisagées sont :

| Composant | Responsabilité |
| --- | --- |
| Analysis Engine | Analyser le code et construire une représentation intermédiaire indépendante du LLM et des moteurs de vérification : AST, fonctions, dépendances, entrées et sorties, état, branches, exceptions et tests existants. |
| Reasoning Engine | Proposer et organiser propriétés, contrats, invariants, hypothèses, scénarios de défaillance et pistes d'investigation, avec leur provenance et leur statut. |
| Interactive Investigation Engine | Piloter la machine à états, maintenir le modèle et le registre des inconnues, rechercher les réponses, interroger l'utilisateur, persister les décisions et relancer les vérifications concernées. |
| Verification Engine | Fournir une interface commune à des moteurs indépendants et interchangeables, avec des résultats normalisés qui conservent les détails propres à chaque outil. |
| Reporting Engine | Présenter les exigences, hypothèses, questions, tests, contre-exemples, résultats et limites de manière traçable et reproductible. |

Une CLI et une interface MCP sont envisagées pour lancer, inspecter et reprendre une investigation. MCP doit exposer et orchestrer les capacités du système, sans devenir une dépendance de sa logique métier. Les interfaces avec les LLM doivent également rester indépendantes des fournisseurs.

## Modèle d'investigation et traçabilité

Le modèle initial couvre les observations, les inconnues et les propriétés proposées par l'utilisateur. Il reste à l'étendre aux objets suivants :

- `ProgramModel` : représentation intermédiaire du code analysé.
- `Property` : propriété candidate ou retenue comme spécification.
- `Assumption` : hypothèse dont dépend une analyse ou une vérification.
- `Unknown` : information manquante et ses interprétations possibles.
- `ProofObligation` : propriété à établir sous des hypothèses explicites.
- `FailureScenario` : situation susceptible de contredire une propriété.
- `VerificationResult` : résultat, provenance et limites d'une vérification.

Ces objets doivent former un graphe de dépendances permettant de remonter d'un résultat aux sources concernées, aux exigences, aux contrats, aux hypothèses et aux outils utilisés. Le registre des inconnues doit conserver, pour chaque entrée, son identifiant, sa description, son origine, les éléments disponibles, les interprétations possibles, son impact, les obligations affectées, son statut et l'éventuelle question posée.

### Statuts envisagés

Les statuts d'une **propriété** et ceux d'une **vérification** ne sont pas interchangeables :

| Statut de propriété | Signification |
| --- | --- |
| `candidate` | Propriété proposée, non validée. |
| `accepted` | Propriété retenue comme spécification. |
| `supported` | Propriété étayée par des résultats, sans impliquer une preuve universelle. |
| `contradicted` | Propriété contredite par un contre-exemple. |
| `unknown` | État non déterminé. |
| `inconsistent` | Propriété ou ensemble d'hypothèses incohérent. |

| Statut de vérification | Signification |
| --- | --- |
| `proved` | Propriété démontrée dans le cadre formel et sous les hypothèses déclarés. |
| `refuted` | Contre-exemple établi dans le cadre de la vérification. |
| `bounded_verified` | Vérification réussie dans les limites d'exploration indiquées. |
| `tested` | Propriété testée sur les cas effectivement exécutés. |
| `inconclusive` | Résultat non concluant. |
| `blocked` | Vérification impossible faute d'information ou de dépendance nécessaire. |
| `unsupported` | Propriété ou langage non pris en charge par le moteur utilisé. |

Un contre-exemple peut révéler un défaut du programme, une spécification inadéquate, une hypothèse manquante, un environnement mal modélisé ou une limite du moteur. Le raffinement peut s'inspirer de CEGAR (*Counterexample-Guided Abstraction Refinement*), mais ne doit jamais altérer automatiquement une spécification dans le seul but d'éliminer un échec.

## Développement envisagé

Les étapes suivantes décrivent une progression ; seule la première tranche du MVP 1 présentée ci-dessus est actuellement implémentée :

1. **MVP 1 — Investigation interactive :** importer du code Python, analyser son AST et ses dépendances directes, proposer des propriétés avec un LLM, tenir un registre des inconnues, poser des questions, persister l'état et produire un rapport initial. Cette étape ne prétend pas apporter une preuve formelle complète.
2. **MVP 2 — Tests et défaillances :** générer et exécuter des tests, étudier l'intégration de Hypothesis, explorer les valeurs limites et exceptions, réduire les cas d'échec et utiliser le mutation testing.
3. **MVP 3 — Vérification formelle :** choisir un premier backend selon le langage et les capacités nécessaires, créer des obligations de preuve et gérer explicitement les hypothèses et résultats conditionnels.
4. **MVP 4 — Raffinement :** analyser les contre-exemples, préciser les hypothèses et le modèle, réévaluer les questions et relancer les vérifications affectées.
5. **MVP 5 — Systèmes complexes :** étendre progressivement l'analyse aux ensembles de fichiers, aux dépendances externes, à l'état partagé, à la concurrence, aux protocoles et aux invariants interprocéduraux.

L'identification explicite des scénarios non testés n'est pas encore formulée comme exigence de ces MVP ni implémentée. La lecture de tests en référence ne mesure pas leur couverture ; une couverture de lignes ou de branches ne garantit pas non plus la couverture des scénarios métier.

Avant l'implémentation, la première mission consiste à étudier les références existantes, notamment AProver, à comparer les approches du MVP 1 et à identifier ce qui peut être réutilisé. Elle doit déboucher sur une architecture minimale, des interfaces et modèles de données initiaux ainsi qu'un plan découpé en petites étapes vérifiables. Chaque étape d'implémentation doit produire un résultat exécutable, testé et documenté, sans chercher à intégrer tous les moteurs dès le départ.

Le premier parcours cible consiste à fournir une fonction Python et à obtenir une analyse structurée, des propriétés candidates, des inconnues et questions ciblées, puis des scénarios de défaillance, des tests exécutés et l'analyse de leurs éventuels contre-exemples. Le rapport doit distinguer ce qui est établi, réfuté ou non démontré et expliquer les blocages. La génération et l'exécution de tests relèvent de l'étape suivante, et non du MVP 1 seul.

## Qualité et sécurité

Le projet vise du code Python typé, conforme à PEP 8, accompagné de tests unitaires et d'intégration et d'une gestion explicite des erreurs. Les investigations et leurs décisions doivent être persistantes ; les vérifications doivent être reproductibles et documenter les budgets de calcul, les bornes d'exploration et les garanties réellement obtenues.

Le code, les commentaires et la documentation analysés sont des **données non fiables**, pas des instructions pour l'agent. L'exécution du code étudié doit avoir lieu dans un environnement isolé et contrôlé. Les modifications du dépôt analysé doivent elles aussi être contrôlées.

Le dépôt Git constitue la source de vérité pour le code, les tests, les spécifications, la documentation, les décisions d'architecture, les références et les exemples reproductibles. Les décisions structurantes ont vocation à être consignées sous forme d'ADR (*Architecture Decision Records*). Les projets externes seront référencés et analysés plutôt que copiés intégralement dans le dépôt.

## Références à étudier

Ces références sont des pistes d'étude, pas des intégrations ou dépendances déjà disponibles :

- **Vérification et tests :** [AProver](https://github.com/agentic-prover/aprover), [Daikon](https://github.com/codespecs/daikon), [CBMC](https://github.com/diffblue/cbmc), [Kani](https://github.com/model-checking/kani), [Frama-C](https://frama-c.com/), [Dafny](https://github.com/dafny-lang/dafny), [Hypothesis](https://github.com/HypothesisWorks/hypothesis) et [Lean 4](https://lean-lang.org/).
- **Raisonnement, ontologies et interfaces de preuve :** [Euclid-MCP](https://github.com/meob/Euclid-MCP), [owl4agents](https://github.com/leungBH/owl4agents), [Open Ontologies](https://github.com/eejd/mcp-open-ontologies), [OpenProof](https://github.com/mxthematic/openproof) et [rocq-mcp](https://github.com/LLM4Rocq/rocq-mcp).

AProver est une piste pour l'étude de l'orchestration d'un LLM avec des moteurs de vérification. Daikon peut proposer des invariants issus de traces, qui restent des candidats à vérifier. Les approches Prolog ou Datalog, RDF, OWL, SHACL et SPARQL sont également à évaluer selon les besoins de représentation et de raisonnement.
