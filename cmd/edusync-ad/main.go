// Commande edusync-ad — application desktop EduSync AD (Wails v3).
//
// L'interface est intégralement servie par internal/ui (HTML + HTMX) et
// embarquée dans le binaire : aucun fichier externe, aucune dépendance
// JavaScript à installer (ROADMAP_GO_MIGRATION.md §3).
//
// Les appels Go ⇄ JS (services Wails) sont volontairement absents du starter
// kit : le rendu serveur couvre le périmètre Phase 0. Ils seront ajoutés
// avec « wails3 generate bindings » dès que les modules de la Phase 1 en
// auront besoin.
package main

import (
	"fmt"
	"log"
	"net/http"
	"os"

	"github.com/wailsapp/wails/v3/pkg/application"

	"github.com/estemobs/EduSync-AD/internal/ui"
)

// version est renseignée au build :
//
//	go build -ldflags "-X main.version=1.17.1" ./cmd/edusync-ad
var version = "dev"

func main() {
	serveur, err := ui.New(ui.Options{Version: version})
	if err != nil {
		log.Fatalf("EduSync AD: %v", err)
	}
	defer func() {
		if err := serveur.Close(); err != nil {
			log.Printf("EduSync AD: fermeture du journal: %v", err)
		}
	}()

	app := application.New(application.Options{
		Name:        "EduSync AD",
		Description: "Gestion du cycle de vie des comptes Active Directory",
		Assets: application.AssetOptions{
			Handler: http.HandlerFunc(serveur.ServeHTTP),
		},
		Mac: application.MacOptions{
			ApplicationShouldTerminateAfterLastWindowClosed: true,
		},
		Linux: application.LinuxOptions{
			// Désactiver l'accélération GPU pour éviter les problèmes Vulkan/WebKitGTK sur Linux
			// (problème connu Wails v3 beta : VK_SUBOPTIMAL_KHR, écran blanc)
		},
		// DisableDefaultSignalHandler: true, // Garde le handler par défaut pour SIGUSR1 (WebKitGC)
	})

	app.Window.NewWithOptions(application.WebviewWindowOptions{
		Title:     fmt.Sprintf("EduSync AD %s", version),
		Width:     1180,
		Height:    780,
		MinWidth:  940,
		MinHeight: 620,
		URL:       "/",
		Linux: application.LinuxWindow{
			// Désactiver l'accélération GPU (WebKitGTK + Vulkan cause écran blanc sur Linux)
			WebviewGpuPolicy: application.WebviewGpuPolicyNever,
		},
		DevToolsEnabled: true, // Pour debug
	})

	if err := app.Run(); err != nil {
		fmt.Fprintf(os.Stderr, "EduSync AD: %v\n", err)
		os.Exit(1)
	}
}
