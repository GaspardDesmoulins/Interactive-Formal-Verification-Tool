# ADR 0002 : Contrat préalable à l'exécution isolée des tests

## Statut et périmètre

Décision d'architecture pour le MVP 2, **non implémentée comme moteur d'exécution**. L'outil ne lance aujourd'hui ni le code cible ni les tests. La commande de préparation décrite dans le README est uniquement une inspection : elle reste bloquée et n'établit pas que l'hôte est sûr.

Le premier environnement envisagé est un hôte Linux x86-64 dédié, administré et préalablement qualifié. Windows et WSL ne sont pas pris en charge pour cette exécution tant que leurs frontières et les tests ci-dessous n'ont pas été qualifiés. Aucun accès privilégié, changement de groupe, installation ou téléchargement ne doit être effectué automatiquement.

## Modèle de menace

La source, ses dépendances, les tests existants ou générés, leurs noms, leurs sorties et les fichiers qu'ils produisent sont considérés comme **potentiellement malveillants**. Un LLM peut aussi proposer du code erroné ou hostile. Un test ou une importation peut lire des secrets, modifier l'hôte, communiquer avec le réseau, lancer des processus, épuiser les ressources, produire des sorties trompeuses ou exploiter le noyau ou l'hyperviseur.

Le contrôleur hôte, son système, ses opérateurs et les images de l'environnement invité sont supposés fiables et maintenus ; la compromission de l'hôte, les attaques physiques et les canaux auxiliaires matériels ne sont pas éliminés par ce contrat. Les images, le noyau invité et les exécutables du backend doivent avoir une provenance, des versions et des empreintes contrôlées. L'isolation réduit le risque, elle ne garantit pas l'absence de vulnérabilité de KVM, de l'hyperviseur ou du noyau.

## Décision de confinement

Retenir comme **backend candidat, pas encore activé**, une microVM Firecracker distincte par exécution, reposant sur KVM, démarrée via le Jailer officiel avec seccomp par défaut, réduction des privilèges, namespaces et cgroup v2. Le service hôte de lancement doit être provisionné séparément par l'administrateur : l'application et le LLM n'obtiennent ni privilèges administrateur, ni accès au socket de contrôle du backend. Le backend doit vérifier effectivement ses contraintes avant tout démarrage ; un nom de profil, un binaire présent ou la seule existence de `/dev/kvm` ne suffisent pas.

Le système invité reçoit uniquement une image de base vérifiée en lecture seule et une copie bornée, propre à l'exécution, des entrées explicitement autorisées. Aucun montage du dépôt, du répertoire personnel, du socket Docker, des identifiants, de l'agent SSH ou du système de fichiers hôte n'est autorisé. Aucune carte réseau, interface TAP, service de métadonnées ni canal vsock n'est fourni. Le loopback propre à l'invité n'est pas assimilé à un accès au réseau hôte. Les sorties invité sont des données non fiables, soumises à des limites strictes.

Firecracker ne filtre pas lui-même le trafic sortant : toute future prise en charge du réseau exigera une décision distincte et un contrôle hôte vérifiable. Les chemins et répertoires utilisés par le Jailer doivent être protégés contre les modifications non autorisées. Le Jailer requiert une configuration explicite : notamment, en cgroup v2, il peut continuer sans placement dans le cgroup demandé si le parent n'existe pas. Le contrôleur doit vérifier le placement et les limites effectifs, et refuser le lancement en cas de doute.

Un conteneur Docker ordinaire, `subprocess`, `venv`, `chroot`, une limite de temps seule ou un simple `unshare` ne constituent pas ce backend. Docker partage le noyau hôte et son démon représente une frontière privilégiée ; être membre du groupe Docker ou constater un profil seccomp ne démontre pas l'isolement requis. Aucun repli automatique vers ces mécanismes n'est autorisé.

## Contrat des demandes et des budgets

Une future demande doit identifier l'investigation et sa révision, la source et son empreinte, une propriété et un scénario existants, ainsi qu'un ensemble fermé de fichiers et un adaptateur de test versionné. Une simple description textuelle de scénario n'est pas un test exécutable. Une approbation explicite doit porter sur ces entrées et leur budget ; ni l'acceptation d'une propriété ni une instruction du LLM ne valent autorisation. Les liens symboliques, chemins de traversée, fichiers spéciaux et archives dangereuses doivent être refusés lors de la préparation ; les empreintes des copies doivent être contrôlées après copie et avant lancement. La résolution des dépendances doit être explicite et hors ligne.

Les limites de la première interface de préparation sont des **bornes de politique**, pas des quotas déjà appliqués :

| Paramètre | Défaut | Bornes inclusives |
| --- | ---: | ---: |
| Temps écoulé maximal en secondes | 30 | 1–60 |
| Temps CPU maximal en secondes | 10 | 1–60 |
| vCPU | 1 | 1–2 |
| Mémoire invitée en Mio | 128 | 128–512 |
| Mémoire maximale du backend hôte en Mio | 256 | 256–768 |
| Stockage temporaire par exécution en Mio | 64 | 16–128 |
| Sorties et journaux capturés en Kio | 64 | 1–256 |
| Tâches hôte du backend (processus et threads) | 64 | 16–128 |

La limite de mémoire hôte doit dépasser la mémoire invitée d'au moins 128 Mio pour le VMM et les ressources associées. Ces valeurs sont provisoires et devront être qualifiées sur l'hôte retenu. Le temps écoulé doit être mesuré côté hôte avec une horloge monotone et inclure démarrage et exécution ; la terminaison et le nettoyage disposent d'un délai distinct borné. Le temps CPU est cumulatif pour les tâches du backend : un contrôle de débit `cpu.max` ne suffit pas à lui seul, il faut une mesure hôte et un arrêt dont la tolérance de dépassement est documentée et testée. Les limites CPU, mémoire, stockage et sorties doivent être contrôlées hors de l'invité. Un cgroup `pids.max` hôte limite les tâches hôte du backend, **pas** le nombre de processus dans le noyau invité : ceux-ci ne sont bornés indirectement que par les ressources de la VM.

Le backend devra imposer au plus une exécution active par installation jusqu'à définition d'une politique globale de concurrence. Le budget de stockage inclut les copies d'entrée, disques temporaires et journaux de l'exécution, mais pas les images de base partagées en lecture seule. Une limite de taille de fichier ne remplace pas un quota sur l'ensemble des fichiers : stockage, écritures, journaux et sorties doivent être bornés collectivement. Le traitement des sorties doit éviter les blocages sur tube plein, l'épuisement mémoire, les séquences de contrôle et la désérialisation non sûre. Les octets de résultat doivent être traités comme non fiables.

## Résultats, arrêt et nettoyage

Une future exécution doit distinguer :

- `blocked` : aucun démarrage, avec les raisons explicites (autorisation, entrées, backend ou contrôles absents) ;
- `completed` : exécution terminée avec code de sortie et résultat *rapporté* par l'adaptateur (`pass`, `fail` ou `unknown`) ;
- `timed_out`, `resource_exhausted`, `cancelled` et `infrastructure_error` : aucune réussite déduite, avec cause et données partielles bornées.

Un résultat `pass` rapporté par un processus qui a importé du code hostile peut être falsifié ou ne refléter aucune assertion réellement exécutée. Il ne constitue donc ni une preuve, ni une garantie de couverture ; une future interprétation plus forte exigera une frontière de confiance et un protocole de résultat démontrés séparément. Les résultats doivent conserver empreintes, version de l'adaptateur et du backend, paramètres effectifs, durée, sortie bornée, troncature et état de nettoyage. Les résultats et leur stockage ne doivent pas être considérés authentiques sans mécanisme supplémentaire.

À l'expiration ou à l'annulation, le contrôleur termine la microVM et ses ressources associées, puis constate leur arrêt avant de supprimer **uniquement** les ressources identifiées de cette exécution. Le nettoyage doit être idempotent, borné et journalisé ; après erreur ou interruption hôte, l'état reste `cleanup_pending` jusqu'à constat ou intervention explicite, et bloque toute nouvelle exécution. Aucune suppression récursive d'un répertoire partagé ou d'un chemin fourni par la cible n'est autorisée.

## Qualification obligatoire avant activation

Ces tests sont à exécuter sur un hôte de test dédié et provisionné pour le backend ; ils ne sont **pas** considérés comme réussis par la présente décision. Chaque essai utilise des fichiers synthétiques, un identifiant unique et un répertoire temporaire dédié ; il vérifie les limites effectives et les ressources résiduelles. Les tests qui lancent du code invité ne démarrent qu'après validation du confinement.

| Cas reproductible | Résultat exigé |
| --- | --- |
| KVM inaccessible, binaire absent, identité ou cgroup invalide, seccomp ou limite impossible à vérifier | `blocked` ; aucun code invité lancé, aucun repli sur le processus ou Docker hôte. |
| Image ou copie d'entrée modifiée, lien symbolique ou chemin de traversée | Refus avant démarrage ; empreinte et investigation inchangées. |
| Sentinelle synthétique placée sur l'hôte hors des entrées autorisées | L'invité ne peut ni la lire ni la modifier ; vérification côté hôte après arrêt. |
| Tentative d'accès depuis l'invité vers une sentinelle réseau hôte et vers l'extérieur | Aucun accès ; vérifier aussi l'absence de NIC, TAP, vsock et service de métadonnées configurés. |
| Boucle CPU, attente infinie, allocation mémoire, création de nombreux processus et écritures répétées | Bornes effectives constatées ; terminaison dans le délai prévu et résultat non réussi. |
| Flux de sortie plus grand que la limite et absence de lecteur côté invité | Capture bornée avec indication de troncature ; pas de blocage ni d'épuisement hôte. |
| Tests synthétiques rapportant succès, échec, sortie invalide, signal ou résultat falsifié | Statuts distincts ; aucune sortie non fiable n'est élevée au rang de preuve. |
| Annulation, échec de démarrage, plantage du contrôleur et reprise après interruption | Arrêt confirmé ou `cleanup_pending` ; aucune nouvelle exécution tant que les ressources résiduelles ne sont pas traitées. |
| Deux demandes simultanées | Une seule exécution admise ; l'autre est explicitement bloquée sans réutilisation de ressources. |

Pour rendre ces essais comparables, le futur harnais de qualification devra utiliser un profil commun : `wall_seconds=10`, `cpu_seconds=2`, `vcpus=1`, `guest_memory_mib=128`, `host_memory_mib=256`, `storage_mib=64`, `output_kib=64` et `host_pids=32`. Il enregistrera l'horloge monotone hôte à l'admission, les compteurs cgroup effectifs avant et après l'essai, les identifiants de ressources et l'état final de nettoyage. Les images et l'adaptateur utilisés seront identifiés par empreinte. Un hôte incapable d'appliquer ce profil est déclaré non qualifié, pas passé.

Les vecteurs minimaux sont les suivants :

1. Une boucle infinie doit être arrêtée au plus tard 15 secondes après admission, nettoyage compris ; le temps CPU hôte mesuré pour le backend ne doit pas dépasser 2,5 secondes. La limite de débit CPU effective doit également être vérifiée. Ces marges sont des critères de qualification, non des garanties déjà établies.
2. Une sortie synthétique d'au moins 1 Mio doit laisser au plus 64 Kio de sorties et journaux capturés au total et signaler la troncature, sans bloquer la terminaison.
3. Les écritures répétées doivent être interrompues avant que l'ensemble des données temporaires de l'exécution dépasse 64 Mio, hors images de base partagées ; la mesure doit inclure les métadonnées et fichiers auxiliaires retenus par le backend.
4. Les essais de réseau et de fichiers doivent utiliser des sentinelles dédiées et éphémères, dont l'accès et l'intégrité sont vérifiés côté hôte. Aucun chemin ou service personnel réel ne sert de cible.
5. Pour chaque erreur injectée avant le démarrage, vérifier qu'aucune microVM ni processus cible n'a été lancé. Pour une erreur après démarrage, vérifier l'arrêt effectif des ressources par leurs identifiants précis ou le blocage persistant `cleanup_pending`.

L'acceptation d'un backend exige également de vérifier les contrôles host-side sur l'hôte exact et les versions utilisées. Un test négatif limité ne prouve pas l'absence de voie d'évasion.

## État observé sur la machine de développement

Le 30 octobre 2026, WSL 2 sous Ubuntu exposait `/dev/kvm`, mais les vérifications de permission en lecture/écriture échouaient pour l'utilisateur courant ; son ouverture réelle n'a pas été tentée. Les binaires Firecracker et Jailer n'étaient pas trouvés. Docker était joignable et son démon indiquait cgroup v2 et seccomp, ce qui ne satisfait pas ce contrat. Aucun changement de permission ni lancement de microVM n'a été effectué. Ces observations ponctuelles ne sont pas une qualification de l'hôte.

## Sources

- [Firecracker : recommandations de production et configuration du Jailer](https://github.com/firecracker-microvm/firecracker/blob/main/docs/prod-host-setup.md).
- [Firecracker : architecture, barrières et limites réseau](https://github.com/firecracker-microvm/firecracker/blob/main/docs/design.md).
- [Firecracker : fonctionnement et conditions du Jailer](https://github.com/firecracker-microvm/firecracker/blob/main/docs/jailer.md).
- [Linux : interface et hiérarchie cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html).
- [Docker : périmètre de sécurité du démon et des conteneurs](https://docs.docker.com/engine/security/).
