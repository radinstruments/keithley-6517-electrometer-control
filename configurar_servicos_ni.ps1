# Executar como Administrador
# Configura serviços NI-488.2 para reiniciar automaticamente em caso de falha
# Isso evita que o driver entre em estado ruim e precise reiniciar o PC

$services = @("ni488enumsvc", "NiSvcLoc")

foreach ($svc in $services) {
    # Configura restart em caso de falha (1 min, 2 min, 3 min)
    sc.exe failure $svc reset= 86400 actions= restart/60000/restart/120000/restart/180000
    Write-Output "$svc configurado para reiniciar automaticamente em caso de falha"

    # Garante que o serviço está configurado para iniciar com o Windows
    Set-Service -Name $svc -StartupType Automatic -ErrorAction SilentlyContinue
    Write-Output "$svc configurado para iniciar automaticamente"
}

Write-Output ""
Write-Output "Pronto. Agora, se o driver NI entrar em estado ruim:"
Write-Output "1. O Windows reiniciará os serviços automaticamente (até 3 tentativas)"
Write-Output "2. Se ainda assim falhar, rode manualmente:"
Write-Output "   Restart-Service ni488enumsvc -Force"
Write-Output "   Restart-Service NiSvcLoc -Force"
Write-Output "3. Desconecte e reconecte o cabo GPIB-USB-B"
