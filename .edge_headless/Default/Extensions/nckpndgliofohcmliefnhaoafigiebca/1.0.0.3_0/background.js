
var portBackgroundToExe = null;
var dataType = "";
var dataStringResult = "";
var EFFECTIVE_DATA_BEGIN = "EffectiveDataBegin";
var EFFECTIVE_DATA_END = "EffectiveDataEnd";
var EFFECTIVE_DATA = "EffectiveData";
var ERROR_NATIVEHOST_NOT_FOUND = "Specified native messaging host not found.";

console.log("backgroundjs begin");

var STR_FUNCTION_NAME = "FunctionName";

var savedTabID = 0;

var MyRequest = null;
var MySender = null;

//---------------------------------监听WebPage函数------------------------
//background监听WebPage消息
chrome.runtime.onMessageExternal.addListener(function(request, sender) {
		
	MyRequest = request;
	MySender = sender;
	if(null == portBackgroundToExe)
	{
		connect();
	}
	

	if(null != portBackgroundToExe && undefined != portBackgroundToExe)
	{
		request["TabID"] = ""+sender.tab.id;
		savedTabID = sender.tab.id;
		portBackgroundToExe.postMessage(request);
	}
	
});  

chrome.runtime.onMessage.addListener(function(msg) {
	if (msg.joke == "quit")
	{
		messageQuit();
	}    
});//---------------------------------js处理函数------------------------

function onDisconnected() {
  
	console.log("Failed to connect: " + chrome.runtime.lastError.message);
	if ( ERROR_NATIVEHOST_NOT_FOUND == chrome.runtime.lastError.message )
	{
			var msg;
			var data = {"type":MyRequest.Type, "text":"-107---"};
			msg = {"Source":"MWIcbcBack","Data":data, "TabID":MySender.tab.id};
			chrome.tabs.sendMessage(MySender.tab.id,msg);
			savedTabID = 0;
			MyRequest = null;
			MySender = null;
			portBackgroundToExe = null;
	}
	portBackgroundToExe = null;
}

function HexCharToNum(hexChar)
{
	if(hexChar >= 0x30 && hexChar <= 0x39)
	{
		return (hexChar-0x30);
	}
	else if(hexChar >= 0x41 && hexChar <= 0x46)
	{
		return (hexChar-0x41+10);
	}
	else if(hexChar >= 0x61 && hexChar <= 0x66)
	{
		return (hexChar-0x61+10);
	}
	return 0;
}

function HexToAsc(strHex)
{
	var strAsc = ""; 
	var  len = strHex.length;
	for(var i = 0; i < len; i+=2)
	{
		var high = (HexCharToNum(strHex.charCodeAt(i)))<<0x04;
		var low = HexCharToNum(strHex.charCodeAt(i+0x01));
		var hex= high+low;
		strAsc += String.fromCharCode(hex);
	}

	return strAsc;
}

//响应exe发出的消息函数
function onNativeMessage(message) {
	var msg;
	if(undefined != message )
	{
		if("StringResult" == message.EffectiveDataTag)
		{
			dataType = message.Type;
			if (message.Type == "quit")
			{
				dataStringResult = "";	
			}
			else
			{
				dataStringResult += message.StringResult;	
			}					
		}
		if ( "End" == message.End )
		{
			var data = {"type":dataType, "text":dataStringResult};
			msg = {"Source":"MWIcbcBack","Data":data, "TabID":savedTabID};
			
			var tabID = parseInt(message["TabID"]);
			if(null == tabID || undefined == tabID)
			{
				tabID = savedTabID;
			}
			
			chrome.tabs.sendMessage(tabID, msg);
			
			dataType = "";
			dataStringResult = "";
			savedTabID = 0;
		}
	}
	else
	{
	}
	
}

function connect() {
//	if(null == portBackgroundToExe)
	{
		var hostName = "com.google.chrome.icbc.mw";
		portBackgroundToExe = chrome.runtime.connectNative(hostName);
		console.log("portBackgroundToExe in connect:  " + portBackgroundToExe);
			
		//注册exe发出消息的监听函数onNativeMessage
		portBackgroundToExe.onMessage.addListener(onNativeMessage);
		portBackgroundToExe.onDisconnect.addListener(onDisconnected);
	}
	
}

//关闭窗口
function messageQuit(){

	if(null != portBackgroundToExe)
	{
			var message = {"Type": "quit"};
		 portBackgroundToExe.postMessage(message);
	}

}

console.log("backgroundjs end");