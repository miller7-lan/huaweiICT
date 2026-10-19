"""Create runtime directories with private, inherited permissions."""

import os
from pathlib import Path


def ensure_private_directory(path: Path):
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)
        return
    import ntsecuritycon
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        owner = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    system = win32security.ConvertStringSidToSid("S-1-5-18")
    acl = win32security.ACL()
    flags = win32con.CONTAINER_INHERIT_ACE | win32con.OBJECT_INHERIT_ACE
    for identity in (owner, system):
        acl.AddAccessAllowedAceEx(win32security.ACL_REVISION, flags, ntsecuritycon.FILE_ALL_ACCESS, identity)
    # Change only the DACL. Copying owner/audit sections through Set-Acl can
    # require SeSecurityPrivilege even when the caller owns the directory.
    win32security.SetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, acl, None)
    actual = win32security.GetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION).GetSecurityDescriptorDacl()
    allowed = {win32security.ConvertSidToStringSid(sid) for sid in (owner, system)}
    if actual.GetAceCount() != 2 or any(
        win32security.ConvertSidToStringSid(actual.GetAce(i)[2]) not in allowed for i in range(actual.GetAceCount())
    ):
        raise RuntimeError("Private runtime ACL verification failed; no credentials were written")


if __name__ == "__main__":
    ensure_private_directory(Path(__file__).resolve().parents[1] / ".runtime")
    print("Private runtime directory is ready.")
