/* 杰理bmp转换表盘资源库 version 1.0.0 */
declare class Info {
    name: string | undefined;
    offset: number | undefined;
    len: number | undefined;
    constructor(name: string, offset: number, len: number);
}
declare function Uint8ArrayToString(fileData: Uint8Array): string;
declare class PackResFormat {
    m_infos: Info[];
    m_buf: Uint8Array | undefined;
    /**
     *
     */
    getFileData(data: Uint8Array, name: string): Uint8Array | undefined;
    private _parse_pack_data;
    private _getFileData;
    private _parse;
    private _getFileSize;
    private getFileContent;
}

export { PackResFormat, Uint8ArrayToString };
/* follow me on Github! */
