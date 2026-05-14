#requires -version 5.1
param(
    [string]$Title = "Kinaesthetic AI - game overlay",
    [int]$Seconds = 7200,
    [int]$X = 40,
    [int]$Y = 40,
    [int]$Width = 360,
    [int]$Height = 260
)

$ErrorActionPreference = "SilentlyContinue"

Add-Type -TypeDefinition @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class OverlayTopMost {
  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);
  public static readonly IntPtr HWND_TOPMOST = new IntPtr(-1);
  public const UInt32 SWP_SHOWWINDOW = 0x0040;
  public static int Apply(string titleNeedle, int x, int y, int width, int height) {
    int count = 0;
    EnumWindows(delegate(IntPtr hWnd, IntPtr lParam) {
      if (!IsWindowVisible(hWnd)) return true;
      StringBuilder title = new StringBuilder(512);
      GetWindowText(hWnd, title, title.Capacity);
      if (title.ToString().Contains(titleNeedle)) {
        SetWindowPos(hWnd, HWND_TOPMOST, x, y, width, height, SWP_SHOWWINDOW);
        count++;
      }
      return true;
    }, IntPtr.Zero);
    return count;
  }
}
"@

$stopAt = (Get-Date).AddSeconds($Seconds)
while ((Get-Date) -lt $stopAt) {
    [OverlayTopMost]::Apply($Title, $X, $Y, $Width, $Height) | Out-Null
    Start-Sleep -Milliseconds 900
}
