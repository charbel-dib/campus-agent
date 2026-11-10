# Déplacer ou annuler une séance (enseignant, scolarité)

1. Trouve la séance avec `get_schedule` (jamais d'identifiant deviné). Si plusieurs séances correspondent, demande laquelle.
2. Pour un déplacement sans créneau imposé : `find_free_slots` avec le groupe, la durée et le type de salle de la séance.
3. Appelle `update_event` (ou `cancel_event` avec le motif). Ces outils préviennent déjà les étudiants : n'envoie pas
   d'annonce en plus.
4. Un examen ne se déplace pas par l'assistant : oriente vers la scolarité.
5. Après confirmation, résume la nouvelle date, la salle et le nombre de personnes prévenues.
