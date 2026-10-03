"""Gestion des photos d'identité (M12) — Import, redimensionnement, stockage AD.

Utilise l'attribut AD `jpegPhoto` (standard) pour la photo principale
et `thumbnailPhoto` (max 100 Ko) pour la vignette.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image

# Ratios et limites
TARGET_RATIO = 3 / 4  # Ratio portrait 3:4 (ex: 300x400)
MAX_THUMBNAIL_SIZE_KB = 100  # AD recommande < 100 Ko pour thumbnailPhoto
MAX_DIMENSION = 400  # Max 400px côté long
JPEG_QUALITY = 85  # Qualité JPEG pour compression


@dataclass
class PhotoInfo:
    """Informations sur une photo traitée."""
    data: bytes
    width: int
    height: int
    size_kb: float
    format: str = "JPEG"


class PhotoError(Exception):
    """Erreur liée au traitement des photos."""
    pass


def load_image_from_path(path: Path) -> Image.Image:
    """Charge une image depuis un fichier."""
    try:
        img = Image.open(path)
        # Convertir en RGB si nécessaire (RGBA, P, etc.)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        return img
    except Exception as exc:
        raise PhotoError(f"Impossible de charger l'image {path}: {exc}") from exc


def crop_to_ratio(image: Image.Image, ratio: float = TARGET_RATIO) -> Image.Image:
    """Recadre l'image au ratio cible (par défaut 3:4), centré."""
    width, height = image.size
    current_ratio = width / height
    
    if abs(current_ratio - ratio) < 0.01:
        return image  # Déjà au bon ratio
    
    if current_ratio > ratio:
        # Image trop large -> recadrer les côtés
        new_width = int(height * ratio)
        left = (width - new_width) // 2
        box = (left, 0, left + new_width, height)
    else:
        # Image trop haute -> recadrer le haut/bas
        new_height = int(width / ratio)
        top = (height - new_height) // 2
        box = (0, top, width, top + new_height)
    
    return image.crop(box)


def resize_to_limit(image: Image.Image, max_dimension: int = MAX_DIMENSION) -> Image.Image:
    """Redimensionne si l'image dépasse la dimension max."""
    width, height = image.size
    if width <= max_dimension and height <= max_dimension:
        return image
    
    if width > height:
        new_width = max_dimension
        new_height = int(height * max_dimension / width)
    else:
        new_height = max_dimension
        new_width = int(width * max_dimension / height)
    
    return image.resize((new_width, new_height), Image.Resampling.LANCZOS)


def optimize_jpeg(image: Image.Image, max_kb: int = MAX_THUMBNAIL_SIZE_KB, 
                  quality: int = JPEG_QUALITY) -> bytes:
    """Compresse en JPEG sous la taille limite en ajustant la qualité."""
    if quality < 10:
        # Dernier recours : réduire les dimensions
        image = resize_to_limit(image, max_dimension=200)
        quality = JPEG_QUALITY
    
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    data = buffer.getvalue()
    size_kb = len(data) / 1024
    
    if size_kb > max_kb and quality > 10:
        # Réessayer avec qualité réduite
        return optimize_jpeg(image, max_kb, quality - 5)
    
    return data


def process_photo(image: Image.Image) -> PhotoInfo:
    """Pipeline complet : recadrage 3:4 -> redimensionnement -> compression JPEG."""
    # 1. Recadrer au ratio 3:4
    img = crop_to_ratio(image, TARGET_RATIO)
    
    # 2. Redimensionner si trop grand
    img = resize_to_limit(img, MAX_DIMENSION)
    
    # 3. Compresser en JPEG < 100 Ko
    data = optimize_jpeg(img, MAX_THUMBNAIL_SIZE_KB)
    
    return PhotoInfo(
        data=data,
        width=img.width,
        height=img.height,
        size_kb=len(data) / 1024,
        format="JPEG"
    )


def process_photo_from_path(path: Path) -> PhotoInfo:
    """Charge, traite et retourne une photo prête pour l'AD."""
    image = load_image_from_path(path)
    return process_photo(image)


# -- Mappage fichiers -> utilisateurs ----------------------------------------

@dataclass
class PhotoMapping:
    """Résultat du mappage fichiers/photos -> utilisateurs."""
    matched: dict[str, Path]  # identifiant ou "prenom_nom" -> chemin fichier
    unmatched_files: list[Path]
    unmatched_users: list[str]


def map_photos_convention(
    photo_dir: Path, 
    users: list[dict],  # Liste d'utilisateurs avec clés: identifiant, prenom, nom
    patterns: list[str] | None = None
) -> PhotoMapping:
    """Mappe les fichiers photos aux utilisateurs selon des conventions de nommage.
    
    Patterns supportés (par défaut):
    - prenom_nom.jpg
    - identifiant.jpg
    - prenom.nom.jpg
    - nom_prenom.jpg
    
    Args:
        photo_dir: Dossier contenant les photos
        users: Liste des utilisateurs (dicts avec identifiant, prenom, nom)
        patterns: Patterns personnalisés (optionnel)
    
    Returns:
        PhotoMapping avec correspondances trouvées
    """
    if patterns is None:
        patterns = [
            "{prenom}_{nom}",
            "{identifiant}",
            "{prenom}.{nom}",
            "{nom}_{prenom}",
        ]
    
    # Indexer les fichiers par nom de base (sans extension)
    photo_files = {}
    for ext in (".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"):
        for f in photo_dir.rglob(f"*{ext}"):
            stem = f.stem.lower()
            if stem not in photo_files:
                photo_files[stem] = f
    
    matched = {}
    used_files = set()
    
    for user in users:
        identifiant = user.get("identifiant", "").lower()
        prenom = user.get("prenom", "").lower()
        nom = user.get("nom", "").lower()
        
        # Construire les clés possibles pour cet utilisateur
        keys = []
        for pattern in patterns:
            key = pattern.format(
                identifiant=identifiant,
                prenom=prenom,
                nom=nom,
            ).lower()
            keys.append(key)
        
        # Chercher une correspondance
        for key in keys:
            if key in photo_files and key not in used_files:
                matched[identifiant] = photo_files[key]
                used_files.add(key)
                break
    
    unmatched_files = [f for stem, f in photo_files.items() if stem not in used_files]
    unmatched_users = [
        u.get("identifiant", "") 
        for u in users 
        if u.get("identifiant", "").lower() not in matched
    ]
    
    return PhotoMapping(
        matched=matched,
        unmatched_files=unmatched_files,
        unmatched_users=unmatched_users,
    )


def map_photos_from_csv(
    csv_path: Path,
    photo_dir: Path,
    users: list[dict],
    identifiant_col: str = "identifiant",
    photo_col: str = "photo",
) -> PhotoMapping:
    """Mappe photos via un fichier CSV (colonne identifiant + colonne nom fichier photo)."""
    import csv
    
    # Lire le CSV
    mapping = {}
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        # Détecter le délimiteur
        sample = f.read(4096)
        f.seek(0)
        import csv as csv_module
        try:
            dialect = csv_module.Sniffer().sniff(sample, delimiters=",;\t|")
            delimiter = dialect.delimiter
        except csv_module.Error:
            delimiter = ";"
        
        reader = csv.DictReader(f, delimiter=delimiter)
        for row in reader:
            ident = row.get(identifiant_col, "").strip()
            photo_file = row.get(photo_col, "").strip()
            if ident and photo_file:
                mapping[ident.lower()] = photo_file
    
    # Résoudre les chemins
    matched = {}
    unmatched_files = []
    unmatched_users = []
    
    for user in users:
        ident = user.get("identifiant", "").lower()
        if ident in mapping:
            photo_path = photo_dir / mapping[ident]
            if photo_path.exists():
                matched[ident] = photo_path
            else:
                unmatched_files.append(photo_path)
        else:
            unmatched_users.append(user.get("identifiant", ""))
    
    # Fichiers référencés dans le CSV mais pas trouvés
    for ident, fname in mapping.items():
        path = photo_dir / fname
        if not path.exists():
            unmatched_files.append(path)
    
    return PhotoMapping(
        matched=matched,
        unmatched_files=unmatched_files,
        unmatched_users=unmatched_users,
    )


# -- Widget Qt pour aperçu photo ---------------------------------------------

def create_photo_preview_pixmap(photo_data: bytes, size: int = 80) -> Optional["QPixmap"]:
    """Crée un QPixmap depuis des données JPEG pour affichage Qt."""
    try:
        from PyQt6.QtGui import QPixmap
        pixmap = QPixmap()
        pixmap.loadFromData(photo_data)
        if not pixmap.isNull():
            return pixmap.scaled(
                size, size,
                aspectRatioMode=Qt.AspectRatioMode.KeepAspectRatio,
                transformMode=Qt.TransformationMode.SmoothTransformation
            )
    except Exception:
        pass
    return None


def photo_to_qicon(photo_data: bytes, size: int = 32) -> Optional["QIcon"]:
    """Convertit des données photo en QIcon pour Qt."""
    try:
        from PyQt6.QtGui import QIcon, QPixmap
        pixmap = QPixmap()
        pixmap.loadFromData(photo_data)
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                size, size,
                aspectRatioMode=Qt.AspectRatioMode.KeepAspectRatio,
                transformMode=Qt.TransformationMode.SmoothTransformation
            )
            return QIcon(scaled)
    except Exception:
        pass
    return None


# Import Qt pour les annotations de type
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QIcon