# Renouvellement automatique par Gmail / Automatic Gmail renewal

## Français

Ce fork de HydroQC 0.9.1 fonctionne entièrement dans Home Assistant.
Il cible HA 2026.11.0 et Python 3.14.2 ou ultérieur.

1. Utilisez Gmail comme adresse du compte Hydro-Québec.
2. Activez la validation en deux étapes Google et générez un mot de passe
   d'application pour Home Assistant. Le mot de passe Gmail habituel ne convient pas.
3. Dans l'entrée HydroQC existante, choisissez **Reconfigurer**, entrez la nouvelle
   adresse et sélectionnez le même contrat. Terminez la première connexion
   manuellement. Ne supprimez pas l'intégration.
4. Dans les **Options**, activez **Renouvellement automatique par Gmail**, puis
   entrez l'adresse Gmail et le mot de passe d'application dans le formulaire masqué.
   Un champ de mot de passe vide conserve celui déjà enregistré.

La vérification des options teste uniquement la connexion et l'accès à INBOX :
elle ne lit aucun message et ne demande aucun code Hydro.

La session sauvegardée est réutilisée d'abord. Lorsque Hydro exige un nouveau
code, le module relève la position de la boîte avant d'en demander l'envoi.
Il vérifie l'expéditeur exact et l'objet français du courriel de vérification.
Il accepte les messages texte ou HTML avec un seul code à six chiffres distinct.
Les anciens messages et les contenus ambigus sont exclus. Un message déjà lu
reste admissible s'il est nouveau pour cette demande.

Le code est soumis dans la session HydroQC existante. Après validation et
sauvegarde de la session, le seul courriel utilisé est déplacé à la corbeille si
l'option est activée. La corbeille n'est jamais purgée. Un échec de nettoyage ne
déconnecte pas une session renouvelée.

Les vérifications sont espacées de cinq secondes, pendant 120 secondes maximum.
Un code expiré permet un seul nouvel envoi. Les autres échecs repassent à la
réauthentification manuelle. Une temporisation persistée de 15 minutes empêche
les rechargements successifs de multiplier les demandes. La première connexion
et la reconfiguration restent manuelles et interrompent une récupération automatique.

Les identifiants sont conservés dans la configuration HA et ses sauvegardes,
jamais dans ce dépôt. Le mot de passe d'application permet l'accès à la boîte :
le filtre Hydro est une limite du code, pas une permission Gmail limitée à un
expéditeur. Les codes et corps de courriels ne deviennent jamais des états ou
événements HA et ne sont pas journalisés.

Si Gmail refuse la connexion, vérifiez le mot de passe d'application, que Google
révoque notamment après un changement du mot de passe principal. Vérifiez aussi
que les codes arrivent dans INBOX, et non dans les indésirables ou un dossier
qui contourne la boîte de réception.

Avant installation, sauvegardez HA et le dossier de code officiel. Installez
l'archive du fork dans le dossier existant de l'intégration et redémarrez Core.
Pour revenir à l'officiel : désactivez l'option automatique, remettez le code
officiel archivé et redémarrez Core. Conservez l'entrée et le contrat pour
préserver les identifiants d'entités et l'historique. Aucune restauration de
base de données n'est nécessaire pour simplement remplacer le code.

Le client web est conservé par défaut. Le client mobile de HydroQC 0.9.1 est
disponible dans les options avancées; changer de client conserve Gmail mais peut
exiger une nouvelle connexion.

## English

This fork adds opt-in Gmail MFA renewal to HydroQC 0.9.1, entirely inside Home
Assistant. It targets HA 2026.11.0 and Python 3.14.2 or later.

Set the Hydro account email to Gmail. Enable Google 2-Step Verification and create
an app password. Reconfigure the existing HydroQC entry with the new email and
the same contract, completing the first login manually. Do not remove the entry.
In Options, enable automatic Gmail renewal and enter the Gmail address and app
password. A blank password field keeps the stored password. Saving options tests
IMAP access without reading messages or requesting a Hydro code.

The saved session is tried first. When MFA is required, INBOX is checkpointed
before requesting a code, then polled every five seconds for up to 120 seconds.
Only a new message from the exact Hydro sender, with the expected French subject
and one distinct six-digit code, is accepted. Read messages are eligible.
Old messages and ambiguous codes are excluded. The existing Hydro client submits
the code. After acceptance and session persistence, only the used email is
optionally moved to Trash. Trash is never emptied. Cleanup failure does not
invalidate a successful login.

An expired code may be resent once. Other failures stop automatic requests and
fall back to manual reauthentication. A persistent 15-minute cooldown prevents
reload loops. Initial setup/reconfiguration remain manual and interrupt pending
automatic renewal.

The app password grants mailbox access; filtering is not a Gmail permission
boundary. Credentials live in HA configuration and backups, never in this repo.
Codes and email bodies are not logged or published as HA states/events. Check
app-password revocation and INBOX delivery when troubleshooting.

Back up HA and the original code before installing a fork release in the existing
integration folder and restarting Core. To roll back, disable automatic renewal,
restore the original code and restart Core. Keep the existing entry and contract
to preserve entity identifiers and history; a database restore is unnecessary.

The web client remains the fork default to preserve existing sessions. Upstream
0.9.1 mobile authentication is available in advanced options. Switching clients
preserves Gmail settings but may require a new sign-in.
