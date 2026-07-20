Correctif ciblé sur un blocage réel rencontré en usage, plus un petit ajustement d'ergonomie.

### Message "OU non vide" enfin explicite

- Impossible de supprimer une OU alors qu'elle semblait vide : en réalité, le groupe de classe créé automatiquement par les Modules 1/2 vit dans la même OU que la classe elle-même — supprimer tous les élèves ne suffit donc pas à vider l'OU tant que ce groupe existe encore, mais l'appli ne le disait pas. Le message liste désormais concrètement les objets qui bloquent (ex. `CN=6emeB`), au lieu d'un texte générique.

### Retour visuel sur les boutons du journal de l'application

- **Copier**, **Vider le journal**, **Exporter…** confirment maintenant visuellement (flash vert) que le clic a bien été pris en compte.
