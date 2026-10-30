# ADR 0001 : Première tranche d'investigation

## Contexte

Le dépôt part d'une page de vision. Le premier objectif est une investigation Python interactive et persistante, sans confondre analyse syntaxique, spécification acceptée et preuve. La documentation d'[AProver](https://github.com/agentic-prover/aprover), consultée le 30 octobre 2026, décrit une génération de contrats pilotée par LLM, une vérification bornée avec CBMC ou Kani et un raffinement guidé par les contre-exemples. Ses parcours de contrats ciblent C et Rust, pas le premier périmètre Python de ce projet. Son orchestration et sa traçabilité constituent des références à étudier, pas une intégration retenue à ce stade.

## Décision

Commencer avec la bibliothèque standard Python, `ast`, une CLI et un instantané JSON versionné. L'analyse n'importe ni n'exécute le fichier cible. Les observations syntaxiques restent distinctes des exigences ; des questions sur le comportement et les paramètres de chaque fonction de premier niveau enregistrent explicitement l'absence de spécification validée, même en présence d'une docstring. Une réponse est une décision enregistrée, pas une propriété formalisée ni une preuve.

Cette tranche permet d'exercer les transitions `open` → `answered` et la reprise avant de choisir un fournisseur de LLM ou un moteur formel. Les fonctions de premier niveau sont volontairement le seul périmètre initial. La représentation conserve le chemin et l'empreinte du contenu analysé ; les réponses sont bloquées si la source a changé ou n'est plus lisible.

## Alternatives et conséquences

- Intégrer directement AProver ou un moteur formel ne résout pas la première étape Python interactive et introduit des dépendances et des choix de vérification prématurés.
- Connecter d'emblée un LLM rendrait le premier parcours dépendant d'un fournisseur et de ses identifiants avant de disposer d'un état d'investigation testable.
- Une analyse AST est reproductible et ne nécessite aucune exécution du code cible, mais ne résout ni les appels dynamiques ni les comportements réels. Elle ne recherche pas encore les réponses dans les tests ou la documentation, et les questions restent génériques.

## Prochaines étapes

1. Étendre le modèle et les tests à la provenance des propriétés, hypothèses et éléments probants.
2. Définir une interface de raisonnement indépendante du fournisseur, puis proposer des questions ciblées à partir des sources, sans traiter leurs contenus comme des instructions.
3. Relier les réponses aux propriétés et obligations, détecter les incohérences et gérer les changements du code source.
4. Ajouter des scénarios de défaillance et des tests isolés avant de choisir et d'intégrer un backend formel adapté.
