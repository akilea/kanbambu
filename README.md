# kanbambu

Petit kanban auto-hébergé pour les impressions 3D. Une carte = une impression à faire, un projet = une catégorie.
Aucune connexion au cloud Bambu Lab ni à l'imprimante.

```
Queue → Printing → Done (✓ / ✗), avec les pièces du projet à gauche
```

## Lancer

```bash
docker compose up -d --build
# http://<serveur>:8080
```

Les données (SQLite + fichiers) vivent dans `./data`. Pour sauvegarder, copie ce dossier.

### Mise à jour automatique (Windows)

`tools\kanbambu-windows.bat` lance kanbambu sans fenêtre et vérifie GitHub toutes les 5 minutes :
s'il y a du nouveau, il fait `git pull` (et `pip install` si les dépendances ont changé), puis redémarre le
serveur. Pour qu'il démarre avec Windows : Win+R → `shell:startup` → y mettre un raccourci vers ce fichier.
Journal : `data/kanbambu.log`. Pour l'arrêter : `tools\kanbambu-stop.bat` (ou Gestionnaire des tâches →
`pythonw.exe`).
Ailleurs : `python tools/serve.py`.

Sans Docker :

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

## Utilisation

| Action | Comment |
| --- | --- |
| Nouveau projet | bouton ➕ en haut |
| Image, lien, fichier Bambu, BOM du projet | ✏️ sur la ligne du projet |
| Réordonner les projets | glisser la poignée ⋮⋮ |
| Plier / déplier | clic sur l'en-tête du projet |
| Nouvelle carte | ➕ sous Queue, **ou glisser des STL/3MF sur la colonne** (une carte par fichier) |
| Ajouter un fichier à une carte | glisser le fichier sur la carte, ou dans sa fiche |
| Déplacer | glisser la carte entre les colonnes |

### Sur le cellulaire

Une colonne à la fois, choisie par les onglets en haut : 🧩 Pièces, Queue, Printing, Done (avec le nombre de cartes).
Chaque carte a un gros bouton pour passer à l'étape suivante (🖨️ vers Printing, 🏁 vers Done) : pas besoin de
glisser. Dans la fiche d'une carte, les trois boutons Queue / Printing / Done la déplacent aussi (sur ordinateur
également). Glisser reste possible pour réordonner (appui long).

### Fichiers STL

Un STL ne contient pas de slicing : il reste joint à la carte (pour l'avoir sous la main), et kanbambu en dessine
un **aperçu** dans le navigateur (three.js, chargé seulement au besoin) qui devient l'image de la carte. Dans la
fiche d'un projet, chaque STL du dossier peut devenir une carte d'un clic. Le temps et le filament viennent
toujours d'un `.gcode.3mf` (ou du temps entré à la main).

### Slicing, automatiquement

Dans Bambu Studio, tranche la plaque puis **File → Export → Export plate sliced file** (`.gcode.3mf`).
Glisse ce fichier sur la carte : kanbambu lit le temps, le poids et les grammes par couleur, prend la
miniature comme aperçu.

Un `.3mf` de projet normal ne contient pas les grammes. Il donne seulement la miniature.

Chaque carte affiche le **temps et le poids d'un plateau** (⏱ 1h40 ⚖ 48g), suivis de `×N` quand il reste
plusieurs plateaux (le total restant est dans l'infobulle). Sans slicing, la fiche de la carte permet d'entrer
le temps à la main (`1h30`, `90m`, `1:30`) ; un `.gcode.3mf` ajouté ensuite le remplace.

L'en-tête affiche le total de filament par couleur pour tout ce qui est en Queue et Printing (plateaux restants).

### Epics : un groupe de projets

Quand une construction tient dans plusieurs fichiers Bambu, crée un **epic** (bouton ➕🗂 en haut) : il regroupe
plusieurs projets, un par `.3mf`.

- **Glisse des `.3mf` sur l'en-tête de l'epic** (ou dans sa fiche) : un projet par fichier, avec ses plateaux,
  pièces et aperçus. Un `.gcode.3mf` « tous les plateaux » donne aussi les temps et le filament.
- Pour ranger un projet existant : glisse-le par sa poignée ⋮⋮ dans l'epic (ou au-dessus pour l'en sortir), ou
  choisis l'epic dans la fiche du projet.
- L'en-tête de l'epic additionne ses projets : temps et poids restant / total, pièces produites / à produire.
- **Exemplaires** : ceux de l'epic multiplient ceux de chaque projet (epic ×2, projet ×3 → 6), pièces et BOM comprises.
- Supprimer un epic garde ses projets.

### Dossiers

Chaque epic et chaque projet a un dossier sur le PC qui fait tourner kanbambu. Par défaut, sous le dossier des
modèles (⚙ Réglages, `~/Documents/3DModel` = `C:\Users\<toi>\Documents\3DModel`) :
`3DModel/<epic>/<projet>/`, ou `3DModel/<projet>/` hors epic. On peut en choisir un autre dans la fiche.

- 📂 ouvre le dossier dans l'Explorateur **du PC** (il est créé au besoin), même si tu cliques depuis le cell.
- La fiche liste les fichiers 3D du dossier : un clic sur un `.3mf` en fait le fichier du projet (plateaux,
  pièces), un clic sur un `.gcode.3mf` lit le slicing.
- Dans la fiche d'un epic : chaque `.3mf` du dossier devient un projet d'un clic (ou « tous »).
- Quand un fichier importé du dossier est enregistré à nouveau dans Bambu Studio, le projet affiche ↻ : un clic
  le relit (plateaux, temps, filament), sans rien re-glisser.

### Surveillance des dossiers

kanbambu surveille les dossiers des epics et des projets (toutes les ~4 s) et ajoute ce que tu y enregistres,
une fois le fichier fini d'écrire. Les fichiers déjà présents la première fois qu'un dossier est vu sont
seulement notés. Une petite notification dit ce qui a été fait, et les pages ouvertes se mettent à jour seules.

| Enregistré dans… | Effet |
| --- | --- |
| le dossier d'un epic : un nouveau `.3mf` | nouveau projet dans l'epic |
| le dossier d'un projet : un `.3mf`, si le projet n'en a pas | devient son fichier Bambu (plateaux, pièces) |
| le fichier Bambu ou l'export du projet, ré-enregistré | relu |
| un `.gcode.3mf` de plusieurs plateaux (*Export all plate sliced file*) | temps et filament des cartes de plateau |
| un `.gcode.3mf` d'un plateau, un STL, STEP… | joint à la carte du même nom (fichier ou titre), sinon nouvelle carte |

Dans un dossier partagé par plusieurs projets (les `.3mf` d'un epic côte à côte), un fichier va au projet dont il
porte le nom : `stacker.gcode.3mf` pour `stacker.3mf`. À désactiver dans ⚙ Réglages au besoin.

Un projet ou un epic qui a un plateau en impression est légèrement teinté avec une 🖨️ dans son en-tête.

### Projet Bambu : une carte par plateau

Dans la fiche du projet (✏️), ajoute le `.3mf` du projet Bambu Studio. kanbambu lit tous ses plateaux et crée
une carte par plateau, avec le nom du plateau, ses pièces et l'aperçu de Bambu Studio.

**Temps et filament :** Bambu Studio ne garde pas les résultats du slicing dans un projet enregistré
(`slice_info.config` est vide), même après *Trancher tout*. Pour les avoir, fais
**Fichier → Exporter → Exporter tous les fichiers de plateaux tranchés** et glisse ce `.gcode.3mf` dans la
zone ⏱ de la fiche du projet. Chaque carte de plateau reçoit son temps, ses grammes et son coût ; le `.3mf`
du projet reste le fichier ouvert dans Bambu Studio. À refaire après un nouveau slicing.

La fiche du projet montre alors les totaux, comme le panneau « All Plates Stats » de Bambu Studio : filament
par emplacement (m, g, coût), temps par plateau, total et reste. L'en-tête du projet affiche le temps et le
poids qui restent.

Après une modification dans Bambu Studio, remplace le fichier (⬆) : les cartes existantes sont mises à jour
(aperçu, temps, grammes) sans doublons, et leur historique est gardé.

### Pièces et exemplaires

Pour un projet imprimé plusieurs fois (2 AMS holder, un exemplaire par enfant…), kanbambu compte des **pièces**,
pas des plateaux :

- Les **colonnes suivent les impressions** : une carte = un plateau lancé, avec sa composition (« 3 Bras + 1 Clip »).
- Le **projet garde l'inventaire** : pour chaque pièce, produites (vert), dans le pipeline = cartes pas encore en Done
  (bleu), et `+N` à planifier. L'en-tête montre le total 🧩 produites/à produire.
- **Exemplaires ×N** (fiche du projet) : multiplie les besoins. Chaque pièce a sa quantité par exemplaire.

D'où viennent les pièces :
- En ajoutant le `.3mf` du projet, chaque objet devient une pièce (quantité = nombre d'instances dans le projet),
  et chaque carte de plateau reçoit sa composition.
- En glissant un `.gcode.3mf` sur un projet qui a des pièces, la composition est lue des noms d'objets du plateau
  (les objets ignorés à l'impression ne comptent pas). Un objet inconnu crée une pièce.
- Renommer une pièce garde l'ancien nom comme alias : les noms d'objets de Bambu Studio continuent de correspondre.
- Sinon, les boutons − / + dans la fiche de la carte.

À l'arrivée dans **Done**, ✓ propose un compteur par pièce : « 2 Bras sur 3 » = réussite partielle (bordure
pointillée orange). Seules les pièces réussies comptent. Le bouton ↻ renvoie la carte dans Queue avec **seulement les
pièces manquantes**, en demandant ce qui change. ⧉ (fiche de la carte) duplique une impression pour la refaire.

La **BOM** (vis, aimants…) se saisit par exemplaire : avec ×N exemplaires, la fiche affiche `= total` et la colonne
de gauche montre la liste déjà multipliée, à cocher au fur et à mesure.

Done garde les 3 cartes les plus récentes par projet ; `+N` affiche les autres.

### Copies et plateaux (cartes simples)

Dans la fiche d'une carte : 📦 nombre de copies à imprimer, ▦ copies par plateau. kanbambu en déduit le nombre
de plateaux (affiché `fait/total` sur la carte). Quand un `.gcode.3mf` contient plusieurs objets, les copies par
plateau sont remplies automatiquement (modifiable).

Chaque ✓ dans Done compte un plateau ; un ✗ ne compte pas. Tant qu'il reste des plateaux, la carte en Done a une
bordure pointillée orange et un bouton 🖨️+ qui la renvoie directement dans Printing (pas besoin d'expliquer un
changement : ce n'est pas un réessai). Glisser la carte hors de Done fait la même chose. Le temps, le poids et le
total de filament de l'en-tête comptent seulement les plateaux restants.

Une carte = un plateau tranché (`Export plate sliced file`).

### Fin d'une impression

Quand une impression se termine, une seule fenêtre : ✓ ou ✗ (et les pièces réussies), puis **Ensuite** :
🏁 Done ou ↺ Queue. kanbambu présélectionne Queue après un échec ou quand le projet a encore besoin de ce
plateau (pièces encore manquantes, plateaux restants) ; tu peux changer. Pas besoin de passer par Done pour
remettre un plateau en file.

- Glisser de Printing vers Queue ouvre la même fenêtre, avec ↺ « Pas imprimé » pour revenir sans résultat.
- Après un ✗ renvoyé en Queue, la fenêtre demande aussi ce qui change.

### Done et réessais

- Entrer dans **Done** demande ✓ (amélioration possible, optionnelle) ou ✗ (raison obligatoire).
- Ressortir de Done une carte **en échec** = réessai : il faut dire **ce qui change**. Ce texte est attaché à la
  tentative suivante. Après une réussite (complète ou partielle), la carte repart sans question.
- Les compteurs ✓ / ✗ s'affichent sur la carte et sur le projet. L'historique est dans la fiche de la carte.

### Ouvrir dans Bambu Studio

Le bouton ↗ à côté d'un `.3mf` ouvre un lien `bambustudio://open?file=…` (Windows/Linux) ou
`bambustudioopen://…` (macOS). Bambu Studio télécharge alors le fichier depuis kanbambu. Pour ça :

- Bambu Studio doit être le programme associé à ces liens (c'est le cas s'il ouvre les modèles MakerWorld) ;
- le PC doit pouvoir joindre le serveur à l'adresse affichée dans le navigateur (utilise l'IP ou le nom du NAS, pas `localhost`).

Sinon, le bouton ⬇ télécharge le fichier.

## Sécurité

Aucune authentification : à garder sur le réseau local (ou derrière un reverse proxy avec auth).

## Tests

```bash
pip install pytest httpx
KANBAMBU_DATA=$(mktemp -d) python -m pytest tests
```

## Structure

```
app/main.py      API FastAPI + fichiers statiques
app/db.py        SQLite (projects, parts, cards, card_parts, files, attempts)
app/threemf.py   lecture des .3mf / .gcode.3mf Bambu (plateaux, temps, filament, coût, aperçus)
static/          interface (HTML/CSS/JS sans build, SortableJS, icônes Lucide)
tools/serve.py   lancement + mise à jour automatique depuis GitHub
tools/stl-thumb/ source de static/vendor/stl-thumb.js (aperçu STL, three.js) et build.sh
```
