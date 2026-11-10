# HydroQC — Renouvellement automatique du MFA par Gmail

Ce fork ajoute à HydroQC le **renouvellement automatique de la session Hydro-Québec à l’aide des codes reçus dans Gmail**. Après une première connexion manuelle, Home Assistant peut récupérer et soumettre le code lorsque Hydro-Québec exige une nouvelle vérification.

Tout fonctionne dans Home Assistant, sans serveur externe, script à planifier ni intégration IMAP séparée. La fonction est **désactivée par défaut**.

## Merci au projet original

Un grand merci à **Mathieu Dallaire — [@mdallaire](https://github.com/mdallaire)** pour son excellent travail sur [HydroQC pour Home Assistant](https://github.com/hydroqc/hydroqc-ha), ainsi qu’aux personnes qui y contribuent. Ce fork, basé sur la version **0.9.1**, s’appuie sur cette intégration et sa prise en charge du MFA manuel.

Ce README présente uniquement les ajouts du fork. Pour l’installation et la configuration générales de HydroQC, les capteurs, les tarifs, les calendriers et les automatisations, consultez le **[mode d’emploi original](https://github.com/hydroqc/hydroqc-ha#readme)**.

> Cette intégration est développée de façon **indépendante** et n'est **pas supportée par Hydro-Québec**.
>
> **NE CONTACTEZ PAS** le service client d'Hydro-Québec pour des problèmes avec cette intégration.

## Ce que le fork ajoute

- Le choix du client web ou mobile de HydroQC 0.9.1, avec Gmail conservé dans les deux modes.
- Une option Gmail dans les paramètres des entrées utilisant un compte Hydro-Québec.
- La récupération des nouveaux codes de vérification par IMAP avec connexion TLS vérifiée.
- La soumission du code et la sauvegarde de la session renouvelée, réutilisable après un redémarrage.
- Le déplacement facultatif du seul courriel utilisé à la corbeille, après une connexion réussie.
- Un retour à la réauthentification manuelle si la récupération automatique échoue.

La configuration initiale, la reconfiguration et la réauthentification manuelle conservent leurs écrans de saisie du code.

## Comment ça fonctionne

1. **Réutiliser la session.** HydroQC essaie d’abord la session sauvegardée.
2. **Préparer la réception du code.** Si Hydro demande le MFA, le module se connecte à Gmail et relève la position actuelle de la boîte de réception avant de demander l’envoi d’un code.
3. **Repérer le nouveau courriel.** Il consulte uniquement `INBOX`, toutes les **5 secondes**, pendant **120 secondes maximum par demande**. Le message doit provenir de `hydro-quebec@communications.hydro.qc.ca`, porter l’objet « Votre code de vérification pour votre identifiant Hydro-Québec » et contenir un seul code distinct à six chiffres, en texte ou en HTML.
4. **Renouveler la connexion.** Le code est soumis avec le même client HydroQC, puis la session obtenue est sauvegardée.
5. **Nettoyer, si demandé.** Le courriel utilisé est déplacé à la corbeille uniquement après validation du code et sauvegarde de la session. La corbeille n’est jamais vidée.

Les anciens messages sont exclus. Un nouveau message déjà marqué comme lu reste admissible; la lecture par le module ne le marque pas comme lu. Un contenu ambigu déclenche le secours manuel.

Le renouvellement se produit **lorsque la session expire et que Hydro exige un code**, souvent après environ 24 heures. Il n’y a pas de tâche programmée à une heure fixe ni de surveillance permanente de la boîte Gmail.

## Client web ou mobile

Dans **Configurer / Options → Avancé → Client de connexion**, vous pouvez choisir
le portail web ou le client de l'application mobile ajouté par HydroQC 0.9.1.

Ce fork conserve le **client web par défaut**, pour réutiliser les sessions des
versions précédentes. Le mode mobile propose un jeton de rafraîchissement de
90 jours, prolongé à l'usage; il peut donc réduire les demandes de codes.
Gmail reste disponible lorsqu'une nouvelle vérification est exigée.
Changer de client peut demander une nouvelle connexion; les options Gmail sont conservées.

## Prérequis

- **Home Assistant 2026.11.0 ou ultérieur**, avec **Python 3.14.2 ou ultérieur**.
- Un compte Hydro-Québec dont les codes de vérification arrivent dans une boîte **Gmail**.
- La validation en deux étapes Google activée et un **mot de passe d’application Google**. Le mot de passe Gmail habituel ne convient pas. Consultez les [instructions de Google](https://support.google.com/accounts/answer/185833?hl=fr).
- Les courriels de vérification doivent arriver dans la boîte de réception, sans filtre qui les archive ou les déplace ailleurs.

Cette version utilise Gmail sur `imap.gmail.com:993` et reconnaît l’objet français du courriel Hydro-Québec.

## Installer le fork

Le fork est actuellement en **préversion**.

1. Sauvegardez Home Assistant, y compris sa base de données, et conservez une copie du dossier HydroQC installé pour le retour arrière.
2. Téléchargez **`hydroqc.zip`** dans les [versions de ce fork](https://github.com/vsaintloup/hydroqc-ha/releases).
3. Remplacez le contenu du dossier `/config/custom_components/hydroqc` par celui de l’archive, puis redémarrez Home Assistant Core.

Le fork utilise le même domaine `hydroqc` que le projet original : il remplace son code dans le même dossier. **Conservez votre entrée existante et le même contrat** pour préserver les identifiants d’entités et l’historique.

Pour une première installation de HydroQC, suivez ensuite la [configuration du projet original](https://github.com/hydroqc/hydroqc-ha#configuration).

## Activer Gmail

1. Utilisez votre adresse Gmail comme adresse du compte Hydro-Québec et vérifiez que la connexion au portail fonctionne.
2. Si une entrée HydroQC existe déjà, choisissez **Reconfigurer**, renseignez la nouvelle adresse et conservez le même contrat. Terminez cette connexion manuellement avec le code reçu. Ne supprimez pas l’entrée.
3. Dans **Paramètres → Appareils et services → Hydro-Québec → Configurer / Options**, activez **Renouvellement automatique par Gmail** (`Automatic renewal through Gmail`).
4. À l’étape suivante, entrez l’adresse Gmail et le **mot de passe d’application Google**. Choisissez si le courriel utilisé doit être déplacé à la corbeille; cette option est activée par défaut.
5. Enregistrez. Le formulaire vérifie l’accès IMAP à Gmail sans lire de courriel ni demander de code Hydro. L’entrée est ensuite rechargée.

Le **mot de passe Hydro-Québec** sert à la connexion au portail. Le **mot de passe d’application Google** sert uniquement à l’accès Gmail. Lors d’une modification ultérieure des options, laisser le champ du mot de passe d’application vide conserve celui déjà enregistré.

## En cas d’échec

Un courriel absent, un code refusé ou ambigu, un problème réseau ou un accès Gmail révoqué ramène à la réparation manuelle de Home Assistant. Un code expiré permet **un seul nouvel envoi**. Une temporisation persistée de **15 minutes** évite que des rechargements successifs multiplient les demandes.

Les demandes automatiques sont sérialisées par compte Hydro. Une authentification manuelle ou le déchargement de l’intégration interrompt la récupération en cours. Si le déplacement du courriel à la corbeille échoue, la session renouvelée reste valide.

Pour les problèmes liés à cette fonction, ouvrez un [ticket dans ce fork](https://github.com/vsaintloup/hydroqc-ha/issues).

## Courriels et identifiants

Le mot de passe d’application permet l’accès à la boîte Gmail : le filtrage des courriels Hydro est effectué par le code et ne constitue pas une permission Gmail limitée à cet expéditeur. Les identifiants sont conservés dans la configuration de Home Assistant et ses sauvegardes.

Les codes, les corps de courriels et les mots de passe ne sont pas inscrits dans les journaux du module, les événements ou les états Home Assistant.

## Désactiver ou revenir au projet original

Pour reprendre la vérification manuelle, désactivez **Renouvellement automatique par Gmail** dans les options.

Pour revenir au code original, désactivez d’abord cette option, remettez le dossier HydroQC archivé, puis redémarrez Core. Conservez l’entrée et le contrat; ce remplacement de code ne nécessite pas de restauration globale de Home Assistant.

[Guide détaillé français / English setup guide](docs/gmail-mfa.md) · [Projet original et mode d’emploi](https://github.com/hydroqc/hydroqc-ha#readme) · [Licence AGPL-3.0](LICENSE)
