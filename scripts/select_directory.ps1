param(
    [string]$InitialPath = "",
    [string]$Description = "Select a folder",
    [ValidateSet("Directory", "File")]
    [string]$Mode = "Directory"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

[System.Windows.Forms.Application]::EnableVisualStyles()

# System.Windows.Forms does not expose the Windows 11 folder picker. The COM
# Common Item Dialog does, including the Explorer-style navigation pane.
Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;

[Flags]
internal enum FileOpenDialogOptions : uint
{
    None = 0,
    FOS_NOCHANGEDIR = 0x00000008,
    FOS_PICKFOLDERS = 0x00000020,
    FOS_FORCEFILESYSTEM = 0x00000040,
    FOS_PATHMUSTEXIST = 0x00000800,
    FOS_FILEMUSTEXIST = 0x00001000,
}

internal enum ShellItemDisplayName : uint
{
    FileSystemPath = 0x80058000,
}

[StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
internal struct ComDialogFilterSpec
{
    [MarshalAs(UnmanagedType.LPWStr)]
    public string Name;

    [MarshalAs(UnmanagedType.LPWStr)]
    public string Spec;
}

[ComImport]
[Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IShellItem
{
    void BindToHandler(IntPtr bindContext, ref Guid handlerId, ref Guid interfaceId, out IntPtr value);
    void GetParent(out IShellItem parent);
    void GetDisplayName(ShellItemDisplayName name, out IntPtr value);
    void GetAttributes(uint mask, out uint attributes);
    int Compare(IShellItem other, uint hint, out int order);
}

[ComImport]
[Guid("42f85136-db7e-439c-85f1-e4075d135fc8")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IFileDialog
{
    [PreserveSig]
    int Show(IntPtr owner);
    void SetFileTypes(
        uint count,
        [In, MarshalAs(UnmanagedType.LPArray, SizeParamIndex = 0)]
        ComDialogFilterSpec[] filters);
    void SetFileTypeIndex(uint index);
    void GetFileTypeIndex(out uint index);
    void Advise(IntPtr eventsSink, out uint cookie);
    void Unadvise(uint cookie);
    void SetOptions(FileOpenDialogOptions options);
    void GetOptions(out FileOpenDialogOptions options);
    void SetDefaultFolder(IShellItem folder);
    void SetFolder(IShellItem folder);
    void GetFolder(out IShellItem folder);
    void GetCurrentSelection(out IShellItem selection);
    void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string name);
    void GetFileName(out IntPtr name);
    void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string title);
    void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string label);
    void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string label);
    void GetResult(out IShellItem selection);
    void AddPlace(IShellItem location, uint placement);
    void SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string extension);
    void Close(int result);
    void SetClientGuid(ref Guid clientGuid);
    void ClearClientData();
    void SetFilter(IntPtr filter);
}

[ComImport]
[Guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")]
internal class FileOpenDialog
{
}

public static class NativeWindowsPicker
{
    private const int ErrorCancelled = unchecked((int)0x800704C7);

    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = true)]
    private static extern int SHCreateItemFromParsingName(
        [MarshalAs(UnmanagedType.LPWStr)] string path,
        IntPtr bindContext,
        ref Guid interfaceId,
        out IShellItem shellItem);

    public static string Show(
        string mode,
        string title,
        IntPtr owner,
        string initialPath)
    {
        IFileDialog dialog = null;
        try
        {
            dialog = (IFileDialog)new FileOpenDialog();
            FileOpenDialogOptions options;
            dialog.GetOptions(out options);
            options |= FileOpenDialogOptions.FOS_NOCHANGEDIR
                | FileOpenDialogOptions.FOS_FORCEFILESYSTEM
                | FileOpenDialogOptions.FOS_PATHMUSTEXIST;

            dialog.SetTitle(title);
            if (String.Equals(mode, "File", StringComparison.Ordinal))
            {
                var filters = new[]
                {
                    new ComDialogFilterSpec
                    {
                        Name = "Model files (*.safetensors)",
                        Spec = "*.safetensors",
                    },
                    new ComDialogFilterSpec
                    {
                        Name = "All files (*.*)",
                        Spec = "*.*",
                    },
                };
                dialog.SetFileTypes((uint)filters.Length, filters);
                dialog.SetFileTypeIndex(1);
                options |= FileOpenDialogOptions.FOS_FILEMUSTEXIST;
            }
            else
            {
                options |= FileOpenDialogOptions.FOS_PICKFOLDERS;
            }
            dialog.SetOptions(options);
            SetInitialFolder(dialog, initialPath);

            int result = dialog.Show(owner);
            if (result == ErrorCancelled)
            {
                return null;
            }
            if (result != 0)
            {
                Marshal.ThrowExceptionForHR(result);
            }

            IShellItem selected;
            dialog.GetResult(out selected);
            if (selected == null)
            {
                return null;
            }
            try
            {
                return GetFileSystemPath(selected);
            }
            finally
            {
                ReleaseComObject(selected);
            }
        }
        finally
        {
            ReleaseComObject(dialog);
        }
    }

    private static void SetInitialFolder(IFileDialog dialog, string initialPath)
    {
        if (String.IsNullOrWhiteSpace(initialPath) || !Directory.Exists(initialPath))
        {
            return;
        }

        IShellItem folder;
        Guid shellItemId = typeof(IShellItem).GUID;
        if (SHCreateItemFromParsingName(
                initialPath,
                IntPtr.Zero,
                ref shellItemId,
                out folder) != 0 || folder == null)
        {
            return;
        }
        try
        {
            dialog.SetFolder(folder);
        }
        finally
        {
            ReleaseComObject(folder);
        }
    }

    private static string GetFileSystemPath(IShellItem selected)
    {
        IntPtr path = IntPtr.Zero;
        selected.GetDisplayName(ShellItemDisplayName.FileSystemPath, out path);
        try
        {
            return path == IntPtr.Zero ? null : Marshal.PtrToStringUni(path);
        }
        finally
        {
            if (path != IntPtr.Zero)
            {
                Marshal.FreeCoTaskMem(path);
            }
        }
    }

    private static void ReleaseComObject(object value)
    {
        if (value != null && Marshal.IsComObject(value))
        {
            Marshal.ReleaseComObject(value);
        }
    }
}
'@

# The backend starts this script as a detached, window-less child process, so there is no
# window of ours to own the dialog. Parenting it to whatever happened to be in the
# foreground would make an unrelated application appear to be blocked, so use a hidden
# top-most owner instead: it keeps the dialog above the browser without claiming a window
# that is not ours.
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.Opacity = 0
$owner.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
$owner.Size = New-Object System.Drawing.Size 1, 1
$owner.StartPosition = [System.Windows.Forms.FormStartPosition]::Manual
$owner.Location = New-Object System.Drawing.Point -32000, -32000

try {
    # Shown off-screen so the owner HWND really exists and really is top-most; an owner
    # that was never shown would not lift the dialog above the browser.
    $owner.Show()
    $selectedPath = [NativeWindowsPicker]::Show(
        $Mode,
        $Description,
        $owner.Handle,
        $InitialPath
    )

    if ($null -ne $selectedPath) {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($selectedPath)
        $encoded = [System.Convert]::ToBase64String($bytes)
        [System.Console]::Out.WriteLine("SELECTED:{0}", $encoded)
    }
    else {
        [System.Console]::Out.WriteLine("CANCELLED")
    }
}
finally {
    $owner.Dispose()
}
