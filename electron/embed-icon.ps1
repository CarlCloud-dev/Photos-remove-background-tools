param(
    [Parameter(Mandatory = $true)]
    [string]$ExecutablePath,

    [Parameter(Mandatory = $true)]
    [string]$IconPath
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $ExecutablePath -PathType Leaf)) {
    throw "Application executable not found: $ExecutablePath"
}
if (-not (Test-Path -LiteralPath $IconPath -PathType Leaf)) {
    throw "Icon file not found: $IconPath"
}

Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;

public static class ExecutableIconWriter
{
    private const ushort ResourceLanguage = 0x0409;
    private static readonly IntPtr RtIcon = new IntPtr(3);
    private static readonly IntPtr RtGroupIcon = new IntPtr(14);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr BeginUpdateResource(string fileName, bool deleteExistingResources);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool UpdateResource(
        IntPtr updateHandle,
        IntPtr type,
        IntPtr name,
        ushort language,
        byte[] data,
        uint dataLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool EndUpdateResource(IntPtr updateHandle, bool discard);

    private static ushort ReadUInt16(byte[] bytes, int offset)
    {
        return (ushort)(bytes[offset] | (bytes[offset + 1] << 8));
    }

    private static uint ReadUInt32(byte[] bytes, int offset)
    {
        return (uint)(bytes[offset]
            | (bytes[offset + 1] << 8)
            | (bytes[offset + 2] << 16)
            | (bytes[offset + 3] << 24));
    }

    private static void WriteUInt16(byte[] bytes, int offset, ushort value)
    {
        bytes[offset] = (byte)(value & 0xff);
        bytes[offset + 1] = (byte)(value >> 8);
    }

    private static void WriteUInt32(byte[] bytes, int offset, uint value)
    {
        bytes[offset] = (byte)(value & 0xff);
        bytes[offset + 1] = (byte)((value >> 8) & 0xff);
        bytes[offset + 2] = (byte)((value >> 16) & 0xff);
        bytes[offset + 3] = (byte)((value >> 24) & 0xff);
    }

    private static void Update(IntPtr handle, IntPtr type, int id, byte[] data)
    {
        if (!UpdateResource(handle, type, new IntPtr(id), ResourceLanguage, data, (uint)data.Length))
        {
            throw new Win32Exception(Marshal.GetLastWin32Error());
        }
    }

    public static void WriteIcon(string executablePath, byte[] icoData)
    {
        if (icoData == null || icoData.Length < 6 || ReadUInt16(icoData, 0) != 0 || ReadUInt16(icoData, 2) != 1)
        {
            throw new InvalidDataException("The supplied icon is not a valid ICO file.");
        }

        int iconCount = ReadUInt16(icoData, 4);
        if (iconCount < 1 || 6 + iconCount * 16 > icoData.Length)
        {
            throw new InvalidDataException("The ICO directory is incomplete.");
        }

        IntPtr handle = BeginUpdateResource(executablePath, false);
        if (handle == IntPtr.Zero)
        {
            throw new Win32Exception(Marshal.GetLastWin32Error());
        }

        try
        {
            var groupData = new byte[6 + iconCount * 14];
            Buffer.BlockCopy(icoData, 0, groupData, 0, 6);

            for (int index = 0; index < iconCount; index++)
            {
                int icoEntryOffset = 6 + index * 16;
                int groupEntryOffset = 6 + index * 14;
                uint imageSize = ReadUInt32(icoData, icoEntryOffset + 8);
                uint imageOffset = ReadUInt32(icoData, icoEntryOffset + 12);

                if (imageSize == 0 || imageSize > Int32.MaxValue || imageOffset > icoData.Length || imageOffset + imageSize > icoData.Length)
                {
                    throw new InvalidDataException("An image inside the ICO file is incomplete.");
                }

                var imageData = new byte[(int)imageSize];
                Buffer.BlockCopy(icoData, (int)imageOffset, imageData, 0, (int)imageSize);
                Update(handle, RtIcon, index + 1, imageData);

                // GRPICONDIRENTRY is ICONDIRENTRY without the final DWORD offset;
                // its final WORD is the resource identifier of the image above.
                Buffer.BlockCopy(icoData, icoEntryOffset, groupData, groupEntryOffset, 8);
                WriteUInt32(groupData, groupEntryOffset + 8, imageSize);
                WriteUInt16(groupData, groupEntryOffset + 12, (ushort)(index + 1));
            }

            Update(handle, RtGroupIcon, 1, groupData);
            if (!EndUpdateResource(handle, false))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            handle = IntPtr.Zero;
        }
        finally
        {
            if (handle != IntPtr.Zero)
            {
                EndUpdateResource(handle, true);
            }
        }
    }
}
'@

[ExecutableIconWriter]::WriteIcon(
    [IO.Path]::GetFullPath($ExecutablePath),
    [IO.File]::ReadAllBytes([IO.Path]::GetFullPath($IconPath))
)
