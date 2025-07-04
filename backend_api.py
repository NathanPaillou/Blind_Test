import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from difflib import SequenceMatcher
from collections import defaultdict
import random

app = Flask(__name__)
CORS(app)  # Allow requests from your Flutter app

# --- Spotify credentials ---
CLIENT_ID = "3b3d3f66084a4ed4abd1e1d07268b5c1"
CLIENT_SECRET = "f36a074638ce4569a0801b693b506389"
REDIRECT_URI = "http://127.0.0.1:8501/callback"
SCOPE = "playlist-read-private playlist-read-collaborative playlist-modify-private"

sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope=SCOPE
))

SEUIL_ARTISTE = 0.8
SEUIL_TITRE = 0.7

def est_similaire(a, b, seuil):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= seuil

def extraire_playlist_spotify(url):
    playlist_id = url.split("/")[-1].split("?")[0]
    results = sp.playlist_tracks(playlist_id)
    chansons = []
    while results:
        for item in results['items']:
            track = item['track']
            if track:
                artiste = track['artists'][0]['name']
                titre = track['name']
                chansons.append((artiste, titre))
        if results.get('next'):
            results = sp.next(results)
        else:
            break
    return chansons

def construire_index(playlists):
    presence = {}
    for i, musiques in enumerate(playlists):
        for artiste, titre in musiques:
            trouve = False
            for (a_ref, t_ref) in presence:
                if est_similaire(artiste, a_ref, SEUIL_ARTISTE) and est_similaire(titre, t_ref, SEUIL_TITRE):
                    presence[(a_ref, t_ref)].add(i)
                    trouve = True
                    break
            if not trouve:
                presence[(artiste, titre)] = {i}
    return presence

def regrouper_par_occurrence(presence):
    groupes = defaultdict(list)
    for (artiste, titre), indices in presence.items():
        groupes[len(indices)].append((artiste, titre))
    return groupes

def generer_playlist_melangee(groupes, seuil_minimum, limiter_par_artiste=False):
    chansons = []
    for n in range(seuil_minimum, max(groupes.keys()) + 1):
        chansons.extend(groupes[n])
    random.shuffle(chansons)
    if limiter_par_artiste:
        vus = set()
        filtrees = []
        for artiste, titre in chansons:
            if artiste not in vus:
                vus.add(artiste)
                filtrees.append((artiste, titre))
        chansons = filtrees
    return chansons

def chercher_uri(artiste, titre):
    results = sp.search(q=f"{titre} {artiste}", type="track", limit=1)
    tracks = results.get("tracks", {}).get("items", [])
    if tracks:
        return tracks[0]["uri"]
    return None

def creer_playlist_spotify(nom_playlist, chansons):
    user_id = sp.current_user()["id"]
    new_playlist = sp.user_playlist_create(user=user_id, name=nom_playlist, public=False)
    uris = []
    for artiste, titre in chansons:
        uri = chercher_uri(artiste, titre)
        if uri:
            uris.append(uri)
    if uris:
        sp.playlist_add_items(new_playlist["id"], uris)
        return new_playlist["external_urls"]["spotify"]
    return None


# Stockage temporaire en mémoire (pour usage perso)
session_data = {}

@app.route('/analyze', methods=['POST'])
def analyze():
    data = request.json
    urls = data.get('urls', [])
    if len(urls) < 2:
        return jsonify({'error': 'Merci de fournir au moins deux playlists.'}), 400
    playlists = [extraire_playlist_spotify(url) for url in urls]
    presence = construire_index(playlists)
    groupes = regrouper_par_occurrence(presence)
    session_id = str(hash(tuple(urls)))
    session_data[session_id] = {
        'groupes': groupes,
        'nb_playlists': len(playlists),
        'urls': urls
    }
    stats = {str(n): len(groupes[n]) for n in groupes}
    return jsonify({'session_id': session_id, 'stats': stats, 'nb_playlists': len(playlists)})

@app.route('/generate', methods=['POST'])
def generate():
    data = request.json
    session_id = data.get('session_id')
    seuil = int(data.get('seuil', 2))
    limiter = bool(data.get('limiter', True))
    if session_id not in session_data:
        return jsonify({'error': 'Session inconnue.'}), 400
    groupes = session_data[session_id]['groupes']
    playlist_finale = generer_playlist_melangee(groupes, seuil, limiter)
    # Pour chaque chanson, chercher le preview_url Spotify
    playlist_with_preview = []
    nb_with_preview = 0
    nb_without_preview = 0
    def chercher_infos_chanson(artiste, titre):
        query = f"{titre} {artiste}"
        url = f"https://api.deezer.com/search?q={requests.utils.quote(query)}"
        try:
            resp = requests.get(url, timeout=5)
            data = resp.json()
            for track in data.get('data', []):
                # On prend le premier résultat avec un extrait
                if track.get('preview'):
                    return {
                        'preview_url': track['preview'],
                        'image_url': track['album']['cover_medium'] if track.get('album') else '',
                        'deezer_url': track.get('link', ''),
                        'title': track.get('title', titre),
                        'artist': track['artist']['name'] if track.get('artist') else artiste
                    }
        except Exception as e:
            print(f"[DEEZER][ERREUR] {query} : {e}")
        return {
            'preview_url': '',
            'image_url': '',
            'deezer_url': '',
            'title': titre,
            'artist': artiste
        }

    for artiste, titre in playlist_finale:
        infos = chercher_infos_chanson(artiste, titre)
        print(f"[DEEZER] Recherche: {titre} - {artiste} | preview_url: {infos['preview_url']} | image: {infos['image_url']} | deezer: {infos['deezer_url']}")
        playlist_with_preview.append({
            "title": infos['title'],
            "artist": infos['artist'],
            "preview_url": infos['preview_url'],
            "image_url": infos['image_url'],
            "deezer_url": infos['deezer_url']
        })
        if infos['preview_url']:
            nb_with_preview += 1
        else:
            nb_without_preview += 1
    print(f"[BACKEND] {nb_with_preview} morceaux avec extrait, {nb_without_preview} sans extrait (sur {len(playlist_finale)})")
    session_data[session_id]['playlist_finale'] = playlist_with_preview
    return jsonify({'playlist': playlist_with_preview, 'with_preview': nb_with_preview, 'without_preview': nb_without_preview})

@app.route('/create_playlist', methods=['POST'])
def create_playlist():
    data = request.json
    session_id = data.get('session_id')
    nom = data.get('nom', 'Blind Test')
    if session_id not in session_data or 'playlist_finale' not in session_data[session_id]:
        return jsonify({'error': 'Session ou playlist non trouvée.'}), 400
    playlist_finale = session_data[session_id]['playlist_finale']
    lien = creer_playlist_spotify(nom, playlist_finale)
    if lien:
        return jsonify({'lien': lien})
    else:
        return jsonify({'error': 'Impossible de créer la playlist.'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
